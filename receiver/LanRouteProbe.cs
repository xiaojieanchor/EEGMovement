using System;
using EEGMovement.Core;

// Isolated software fixture runner. Identities supplied by the test, never deployment credentials.
public static class LanRouteProbe
{
    public static int Main(string[] args)
    {
        var transport=new LanHubTransport(args[2],args[3],new DesktopJson());
        var result=transport.Probe(args[0],int.Parse(args[1])).GetAwaiter().GetResult();
        Console.WriteLine(result!=null?"authenticated_http_and_udp":"no_authenticated_route");
        return result!=null?0:2;
    }
}
