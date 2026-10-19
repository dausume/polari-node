"""warm_project - sim-2: write the Maven project the image build uses to FILL its repository (so a jar build at run time is `mvn -o`).

    python3 warm_project.py <framework modules dir> <out dir>

The pom is the generated bridge's OWN pom (grpcbridge.custom.java_bridge_templates.POM_XML), so the warmed plugins and dependencies are exactly the ones a
generated project resolves; the proto and the one class only make the protobuf plugin fetch protoc and the grpc-java plugin executable.
"""
import os
import sys

PROTO = '''syntax = "proto3";
package polari.warm;
option java_package = "org.polari.warm";
option java_multiple_files = true;
message Ping { string name = 1; }
service Warm { rpc Hello (Ping) returns (Ping); }
'''
MAIN = '''package org.polari.bridge;

public final class BridgeMain {
    public static void main(String[] args) {
        System.out.println(org.polari.warm.Ping.newBuilder().setName("warm").build());
    }
}
'''


def main(modules, out):
    sys.path.insert(0, modules)
    from grpcbridge.custom import java_bridge_templates as jt
    files = {'pom.xml': jt.POM_XML.replace('__BRIDGE_NAME__', 'warm'), 'src/main/proto/warm.proto': PROTO,
             'src/main/java/org/polari/bridge/BridgeMain.java': MAIN}
    for path, text in files.items():
        dest = os.path.join(out, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w') as f:
            f.write(text)
    print('warm project: %d files in %s' % (len(files), out))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
