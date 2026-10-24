"""Bounded supervision for an SDK that may block inside a native DLL.

The worker never performs networking or writes EEG files. A bounded pipe carries
copied blocks; if its supervisor stops consuming it, no samples are silently
dropped. Parent-side watchdogs can terminate a blocked SDK process.
"""
from __future__ import annotations

import multiprocessing
import time

from .sdk import ParticipantReader, SDKError


def _discover(connection,settings,reader_factory):
    reader=None
    try:
        reader=reader_factory(**settings);connection.send({'devices':reader.discover()})
    except BaseException as e:
        connection.send({'code':getattr(e,'code',type(e).__name__),'error':str(e),'native_code':getattr(e,'native_code',None)})
    finally:
        if reader:
            try:reader.close()
            except BaseException:pass
        connection.close()


def discover_devices(settings,timeout=5,reader_factory=ParticipantReader):
    context=multiprocessing.get_context('spawn');receive,send=context.Pipe(duplex=False)
    process=context.Process(target=_discover,args=(send,settings,reader_factory),daemon=True);process.start();send.close()
    try:
        if not receive.poll(timeout):raise SDKError('discovery_timeout','设备发现超时；未启动采集。')
        result=receive.recv()
        if 'error' in result:raise SDKError(result['code'],result['error'],native_code=result.get('native_code'))
        return result['devices']
    finally:
        if process.is_alive():process.terminate()
        process.join(.5)
        if process.is_alive():process.kill();process.join(.5)
        receive.close()


def _run_worker(connection, stop_event, settings, reader_factory):
    reader = None
    try:
        reader = reader_factory(**settings)
        descriptor = reader.open()
        connection.send({'kind': 'descriptor', 'descriptor': descriptor})
        while not stop_event.is_set():
            began=time.monotonic_ns();wall_began=time.time_ns()
            samples = reader.read()
            read_time = time.monotonic_ns()
            wall_time = time.time_ns()
            if len(samples):
                connection.send({'kind': 'block', 'samples': samples,
                                 'read_monotonic_ns': began, 'utc_ns': wall_began,
                                 'read_completed_monotonic_ns':read_time,'read_completed_utc_ns':wall_time})
            else:
                stop_event.wait(.002)
    except BaseException as exc:
        try:
            connection.send({'kind': 'error', 'code': getattr(exc, 'code', type(exc).__name__),
                             'message': str(exc), 'native_code': getattr(exc, 'native_code', None)})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if reader is not None:
            try:
                reader.close()
                if getattr(reader, 'close_errors', None):
                    connection.send({'kind': 'state', 'state': 'close_errors', 'errors': reader.close_errors})
            except BaseException as exc:
                try:connection.send({'kind':'state','state':'close_errors','errors':[str(exc)]})
                except (BrokenPipeError,EOFError,OSError):pass
        try:
            connection.send({'kind': 'state', 'state': 'closed'})
        except (BrokenPipeError, EOFError, OSError):
            pass
        connection.close()


class SDKWorker:
    def __init__(self, settings, *, reader_factory=ParticipantReader):
        self.settings = dict(settings)
        self.reader_factory = reader_factory
        self.process = None
        self.connection = None
        self.stop_event = None
        self.forced_termination = False
        self.last_message_monotonic = None
        self.last_stop_messages = []

    @property
    def alive(self):
        return self.process is not None and self.process.is_alive()

    def start(self):
        if self.process is not None:
            raise RuntimeError('SDK worker cannot be started twice')
        # A frozen executable's main entrypoint must call freeze_support(). This
        # library never re-enters a GUI on import and uses a top-level target.
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe(duplex=False)
        self.stop_event = context.Event()
        self.connection = parent
        self.process = context.Process(target=_run_worker,
            args=(child, self.stop_event, self.settings, self.reader_factory),
            name='gNautilus SDK participant', daemon=True)
        self.process.start()
        child.close()
        self.last_message_monotonic = time.monotonic()
        return self

    def receive(self, timeout=0):
        if self.connection is None:
            return None
        try:
            if self.connection.poll(max(0, timeout)):
                value = self.connection.recv()
                self.last_message_monotonic = time.monotonic()
                return value
        except (EOFError, OSError):
            return None
        return None

    def is_stuck(self, timeout):
        return bool(self.alive and self.last_message_monotonic is not None and
                    time.monotonic() - self.last_message_monotonic > timeout)

    def stop(self, timeout=2, on_message=None):
        """Stop this participant, returning pending blocks for the durable writer.

        A caller may instead supply ``on_message`` to commit every drained block
        before closing its journal. Termination is explicit in
        ``forced_termination`` and must be included in the session close record.
        """
        if self.process is None:
            return []
        self.stop_event.set()
        deadline = time.monotonic() + max(0, timeout)
        pending = []
        callback_errors = []
        def keep(message):
            if message is not None:
                if on_message is not None and not callback_errors:
                    try:on_message(message)
                    except BaseException as error:
                        callback_errors.append(error);pending.append(message)
                else:
                    pending.append(message)
        # Drain the pipe while waiting: a producer must not remain blocked on a
        # pending send merely because the UI stopped consuming its last block.
        while self.process.is_alive() and time.monotonic() < deadline:
            keep(self.receive(min(.02, max(0, deadline - time.monotonic()))))
            self.process.join(0)
        # Process exit does not imply its already-written pipe messages were read.
        while time.monotonic() < deadline:
            message = self.receive(0)
            if message is None:
                break
            keep(message)
        if self.process.is_alive():
            self.forced_termination = True
            self.process.terminate()
            self.process.join(.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(.5)
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.last_stop_messages = pending
        if callback_errors:raise callback_errors[0]
        return pending
