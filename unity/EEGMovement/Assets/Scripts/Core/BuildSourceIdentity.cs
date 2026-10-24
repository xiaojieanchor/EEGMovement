using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Security.Cryptography;

namespace EEGMovement.Core
{
    // Shared by the editor build and executable behavior checks; no Unity API or private runtime files.
    public static class BuildSourceIdentity
    {
        static string Normal(string path) { return path.Replace('\\', '/'); }
        static bool IgnoredSource(string path)
        {
            foreach (var part in Normal(path).Split('/'))
                if (part.EndsWith("~", StringComparison.Ordinal) || part.EndsWith("~.meta", StringComparison.Ordinal)) return true;
            return path == "Assets/Resources/build_identity.json" || path == "Assets/Resources/build_identity.json.meta";
        }
        static IEnumerable<string> Walk(string root, string relative, bool sources)
        {
            string directory = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!Directory.Exists(directory)) yield break;
            foreach (string file in Directory.GetFiles(directory))
            {
                string path = Normal(Path.Combine(relative, Path.GetFileName(file)));
                if (sources && IgnoredSource(path)) continue;
                if (!sources && (Path.GetFileName(file) == "build-status.json" || Path.GetExtension(file).Equals(".log", StringComparison.OrdinalIgnoreCase))) continue;
                yield return path;
            }
            foreach (string child in Directory.GetDirectories(directory))
            {
                string path = Normal(Path.Combine(relative, Path.GetFileName(child)));
                if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) != 0 || (sources && IgnoredSource(path))) continue;
                foreach (var file in Walk(root, path, sources)) yield return file;
            }
        }
        static SortedDictionary<string, string> Digest(string root, IEnumerable<string> paths, string prefix, int maximumFiles, long maximumFileBytes, long maximumTotalBytes)
        {
            var result = new SortedDictionary<string, string>(StringComparer.Ordinal); long total = 0;
            foreach (var relative in paths)
            {
                string full = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)); long bytes = new FileInfo(full).Length;
                if (result.Count >= maximumFiles || bytes > maximumFileBytes || (total += bytes) > maximumTotalBytes) throw new IOException("Build identity input exceeds bounded limits.");
                using (var hash = SHA256.Create()) using (var stream = File.OpenRead(full)) result[prefix + relative] = Hex(hash.ComputeHash(stream));
            }
            return result;
        }
        static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
        public static SortedDictionary<string, string> Sources(string projectRoot, string prefix)
        {
            var paths = new List<string>();
            foreach (var directory in new[] { "Assets", "ProjectSettings", "Packages" }) paths.AddRange(Walk(projectRoot, directory, true));
            return Digest(projectRoot, paths, prefix, 4096, 64L * 1024 * 1024, 128L * 1024 * 1024);
        }
        public static SortedDictionary<string, string> Artifacts(string directory)
        {
            return Digest(directory, Walk(directory, "", false), "", 512, 1024L * 1024 * 1024, 1024L * 1024 * 1024);
        }
        public static string Id(SortedDictionary<string, string> files)
        {
            var canonical = new StringBuilder(); foreach (var pair in files) canonical.Append(pair.Key).Append(':').Append(pair.Value).Append('\n');
            using (var hash = SHA256.Create()) return "eeg-" + Hex(hash.ComputeHash(Encoding.UTF8.GetBytes(canonical.ToString()))).Substring(0, 24);
        }
    }
}
