"""Retirement metadata must preserve the original workspace body layout."""
import unittest
from html.parser import HTMLParser
from hub.device_transition import annotate_html


class Children(HTMLParser):
    def __init__(self):
        super().__init__(); self.depth = 0; self.children = []; self.body_attrs = {}
    def handle_starttag(self, tag, attrs):
        if tag == 'body': self.depth = 1; self.body_attrs = dict(attrs)
        elif self.depth:
            if self.depth == 1: self.children.append(tag)
            self.depth += 1
    def handle_endtag(self, tag):
        if self.depth: self.depth -= 1


class WorkspaceTransition(unittest.TestCase):
    def test_archived_page_keeps_sidebar_and_main_as_original_body_children(self):
        original = b'<html><body class="studio"><aside id="studioRail"></aside><main><div id="notice"></div></main></body></html>'
        parser = Children(); parser.feed(annotate_html(original, True).decode())
        self.assertEqual(parser.children, ['aside', 'main'])
        self.assertEqual(parser.body_attrs['data-device-transition'], 'gnautilus')
        self.assertEqual(parser.body_attrs['class'], 'studio')

    def test_nonretired_page_remains_unchanged(self):
        original = b'<body><main>page</main></body>'
        self.assertEqual(annotate_html(original, False), original)


if __name__ == '__main__': unittest.main()
