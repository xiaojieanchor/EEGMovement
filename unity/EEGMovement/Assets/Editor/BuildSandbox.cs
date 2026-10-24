using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Security.Cryptography;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.XR.Management;
using UnityEditor.XR.Management.Metadata;
using UnityEditor.XR.OpenXR;
using UnityEditor.XR.OpenXR.Features;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;
using UnityEngine.XR.OpenXR.Features;
using UnityEngine.XR.OpenXR.Features.Interactions;
using UnityEngine.XR.OpenXR.Features.MetaQuestSupport;
using EEGMovement.Unity;
using EEGMovement.Core;

public static class BuildSandbox
{
    const string Scene = "Assets/Scenes/EEGSandbox.unity";
    const string IdentityPath = "Assets/Resources/build_identity.json";
    const string Prefix = "unity/EEGMovement/";
    [InitializeOnLoadMethod]
    static void OnLoaded()
    {
        EditorApplication.delayCall += () => { if (!File.Exists(Scene) && !EditorApplication.isPlayingOrWillChangePlaymode) CreateScene(); };
    }
    [MenuItem("EEGMovement/Create sandbox scene")]
    public static void CreateScene()
    {
        Directory.CreateDirectory("Assets/Scenes");
        Directory.CreateDirectory("Assets/Resources");
        // Keep the vertex-color line shader in player builds; Shader.Find alone can be stripped.
        if (!AssetDatabase.LoadAssetAtPath<Material>("Assets/Resources/WorkspaceLine.mat"))
            AssetDatabase.CreateAsset(new Material(Shader.Find("Sprites/Default")), "Assets/Resources/WorkspaceLine.mat");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single); SandboxFactory.Build();
        Directory.CreateDirectory("Assets/Materials");
        var materials = new Dictionary<Material, Material>();
        foreach (var renderer in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None))
        {
            Material[] slots = renderer.sharedMaterials;
            for (int i = 0; i < slots.Length; i++)
            {
                var original = slots[i]; if (original == null || AssetDatabase.Contains(original)) continue;
                Material saved;
                if (!materials.TryGetValue(original, out saved))
                {
                    string path = "Assets/Materials/Palette_" + materials.Count + ".mat"; saved = AssetDatabase.LoadAssetAtPath<Material>(path);
                    if (saved == null) { saved = original; AssetDatabase.CreateAsset(saved, path); } else EditorUtility.CopySerialized(original, saved);
                    materials.Add(original, saved);
                }
                slots[i] = saved;
            }
            renderer.sharedMaterials = slots;
        }
        EditorSceneManager.SaveScene(scene, Scene); EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(Scene, true) };
        PlayerSettings.companyName = "EEGMovement"; PlayerSettings.productName = "EEGMovement"; PlayerSettings.bundleVersion = "2.1.4";
        PlayerSettings.defaultScreenWidth = 1440; PlayerSettings.defaultScreenHeight = 900; PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.resizableWindow = true; PlayerSettings.runInBackground = true; AssetDatabase.SaveAssets();
    }
    [MenuItem("EEGMovement/Validate sandbox scene")]
    public static void ValidateScene()
    {
        EditorSceneManager.OpenScene(Scene, OpenSceneMode.Single);
        var avatars = UnityEngine.Object.FindObjectsByType<EEGAvatarController>(FindObjectsSortMode.None);
        if (avatars.Length != 1) throw new Exception("Exactly one applied-pose receiver is required.");
        var ui = UnityEngine.Object.FindFirstObjectByType<WorkspaceUI>();
        if (!ui || ui.controller != avatars[0] || !ui.desktopCamera) throw new Exception("Shared UGUI workspace/camera binding missing.");
        if (avatars[0].Workspace.AllowTests || avatars[0].Workspace.EmergencyLatched) throw new Exception("Scene must start idle, TEST disabled.");
        if (!avatars[0].GetComponent<CharacterController>()) throw new Exception("Actual collision application is required.");
        var font = AssetDatabase.LoadAssetAtPath<Font>("Assets/Resources/Fonts/NotoSansCJKsc-Regular.otf");
        if (!font || !font.HasCharacter('体') || !font.HasCharacter('停')) throw new Exception("Redistributable Chinese glyph coverage missing.");
        if (!File.Exists("Assets/Resources/Fonts/OFL.txt")) throw new Exception("Font redistribution license missing.");
        foreach (var root in EditorSceneManager.GetActiveScene().GetRootGameObjects())
            foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                if (GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(transform.gameObject) != 0) throw new Exception("Missing component: " + transform.name);
        Debug.Log("EEG_SCENE_VALIDATED: one receiver, UGUI/XR workspace binding, actual collision, Chinese font/license, safe defaults, no missing scripts.");
    }
    public static SortedDictionary<string, string> SourceHashes()
    {
        return BuildSourceIdentity.Sources(Directory.GetCurrentDirectory(), Prefix);
    }
    static string Hash(byte[] bytes) { using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static string Quote(string value) { return "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\""; }
    public static string StampIdentity()
    {
        string id = BuildSourceIdentity.Id(SourceHashes());
        Directory.CreateDirectory("Assets/Resources"); File.WriteAllText(IdentityPath, "{\"app_version\":\"2.1.4\",\"build_id\":" + Quote(id) + "}", new UTF8Encoding(false));
        AssetDatabase.ImportAsset(IdentityPath, ImportAssetOptions.ForceSynchronousImport); return id;
    }
    static void ConfigureXR(BuildTargetGroup group)
    {
        const string path = "Assets/XR/EEGXRSettings.asset"; Directory.CreateDirectory("Assets/XR");
        var settings = AssetDatabase.LoadAssetAtPath<XRGeneralSettingsPerBuildTarget>(path);
        if (settings == null) { settings = ScriptableObject.CreateInstance<XRGeneralSettingsPerBuildTarget>(); AssetDatabase.CreateAsset(settings, path); }
        EditorBuildSettings.AddConfigObject(XRGeneralSettings.k_SettingsKey, settings, true);
        if (!settings.HasSettingsForBuildTarget(group)) settings.CreateDefaultSettingsForBuildTarget(group);
        if (!settings.HasManagerSettingsForBuildTarget(group)) settings.CreateDefaultManagerSettingsForBuildTarget(group);
        var general = settings.SettingsForBuildTarget(group); general.InitManagerOnStart = true;
        if (!XRPackageMetadataStore.AssignLoader(general.Manager, "UnityEngine.XR.OpenXR.OpenXRLoader", group)) throw new Exception("Official OpenXR loader could not be assigned.");
        // Official refresh creates the package settings and serialized feature instances on first use.
        // Merely querying GetSettings/GetFeatures can return null or an empty list before that refresh.
        FeatureHelpers.RefreshFeatures(BuildTargetGroup.Standalone);
        FeatureHelpers.RefreshFeatures(BuildTargetGroup.Android);
        var xr = OpenXRSettings.GetSettingsForBuildTargetGroup(group);
        if (xr == null) throw new Exception("Official OpenXR settings were not initialized.");
        var touch = xr.GetFeature<OculusTouchControllerProfile>();
        if (touch == null) throw new Exception("Official Oculus Touch interaction profile is missing.");
        touch.enabled = true; EditorUtility.SetDirty(touch); AssetDatabase.SaveAssetIfDirty(touch);
        if (group == BuildTargetGroup.Android)
        {
            var quest = xr.GetFeature<MetaQuestFeature>();
            if (quest == null) throw new Exception("Official Meta Quest Android support feature is missing.");
            quest.enabled = true; quest.AddTargetDevice("quest3s", "Quest 3S", true);
            EditorUtility.SetDirty(quest); AssetDatabase.SaveAssetIfDirty(quest);
            xr.renderMode = OpenXRSettings.RenderMode.SinglePassInstanced;
            xr.latencyOptimization = OpenXRSettings.LatencyOptimization.PrioritizeInputPolling;
        }
        EditorUtility.SetDirty(xr); EditorUtility.SetDirty(general.Manager); EditorUtility.SetDirty(settings); EditorUtility.SetDirty(general);
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        // Validate the actual enabled object after official refresh and persistence, not a cached name match.
        FeatureHelpers.RefreshFeatures(group);
        if (!OpenXRSettings.GetSettingsForBuildTargetGroup(group).GetFeature<OculusTouchControllerProfile>().enabled)
            throw new Exception("Oculus Touch interaction profile did not remain enabled after refresh.");
        AssetDatabase.SaveAssets();
    }
    static void ValidateOpenXR(BuildTargetGroup group)
    {
        var issues = new List<OpenXRFeature.ValidationRule>();
        OpenXRProjectValidation.GetCurrentValidationIssues(issues, group);
        foreach (var issue in issues)
        {
            if (issue.error) Debug.LogError("EEG_OPENXR_PREFLIGHT: " + issue.message);
            else Debug.LogWarning("EEG_OPENXR_PREFLIGHT: " + issue.message);
        }
        if (issues.Any(issue => issue.error)) throw new Exception("Official OpenXR preflight failed; build identity was not stamped.");
        Debug.Log("EEG_OPENXR_PREFLIGHT_PASS " + group + " interaction-profile=OculusTouch");
    }
    [MenuItem("EEGMovement/Build Windows sandbox")]
    public static void Windows() { Build(BuildTarget.StandaloneWindows64, "EEGMOVEMENT_WINDOWS_BUILD_PATH", "G:/EEGMovement/Windows/EEGMovement.exe"); }
    [MenuItem("EEGMovement/Build Android sandbox")]
    public static void Android()
    {
        ConfigureAndroidPlayer();
        Build(BuildTarget.Android, "EEGMOVEMENT_ANDROID_BUILD_PATH", "G:/EEGMovement/Android/EEGMovement.apk");
    }
    static void ConfigureAndroidPlayer()
    {
        PlayerSettings.bundleVersion="2.1.4";PlayerSettings.Android.bundleVersionCode=21005;
        PlayerSettings.colorSpace = ColorSpace.Linear;
        PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, "org.eegmovement.workspace");
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android, ScriptingImplementation.IL2CPP);
        PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
        PlayerSettings.Android.minSdkVersion = AndroidSdkVersions.AndroidApiLevel32;
        PlayerSettings.Android.applicationEntry = AndroidApplicationEntry.GameActivity;
        PlayerSettings.defaultInterfaceOrientation = UIOrientation.LandscapeLeft;
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android, false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, new[] { GraphicsDeviceType.Vulkan, GraphicsDeviceType.OpenGLES3 });
        EditorUserBuildSettings.buildAppBundle = false;
        PlayerSettings.Android.forceInternetPermission = true;
        PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;
    }
    public static void ValidateAndroidConfiguration()
    {
        ConfigureAndroidPlayer(); ConfigureXR(BuildTargetGroup.Android); ValidateOpenXR(BuildTargetGroup.Android);
        if (PlayerSettings.colorSpace != ColorSpace.Linear) throw new Exception("Android OpenXR requires Linear color space with OpenGLES.");
    }
    static void Build(BuildTarget target, string variable, string fallback)
    {
        // Regenerate unconditionally so a baseline .unity scene can never silently enter the new build.
        var group = target == BuildTarget.Android ? BuildTargetGroup.Android : BuildTargetGroup.Standalone;
        CreateScene(); ValidateScene(); AcquisitionPresentationValidation.Validate(); ConfigureXR(group); ValidateOpenXR(group);
        AssetDatabase.SaveAssets(); AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        string id = StampIdentity(); var sources = SourceHashes();
        string output = Environment.GetEnvironmentVariable(variable); if (string.IsNullOrWhiteSpace(output)) output = fallback;
        output = Path.GetFullPath(output); Directory.CreateDirectory(Path.GetDirectoryName(output));
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions { scenes = new[] { Scene }, locationPathName = output, target = target, options = BuildOptions.None });
        bool built = report.summary.result == BuildResult.Succeeded;
        AssetDatabase.SaveAssets();
        var afterBuild = SourceHashes();
        var sourceChanges = sources.Keys.Union(afterBuild.Keys).Where(path => !sources.ContainsKey(path) || !afterBuild.ContainsKey(path) || sources[path] != afterBuild[path]).OrderBy(path => path, StringComparer.Ordinal).ToArray();
        bool sourceStable = sourceChanges.Length == 0;
        bool success = built && sourceStable;
        var artifacts = built ? BuildSourceIdentity.Artifacts(Path.GetDirectoryName(output)) : new SortedDictionary<string, string>(StringComparer.Ordinal);
        string record = Environment.GetEnvironmentVariable("EEGMOVEMENT_BUILD_RECORD_PATH");
        if (string.IsNullOrWhiteSpace(record)) record = target == BuildTarget.Android ? "G:/Unity/android-build-status.json" : "G:/Unity/build-status.json";
        var sourceJson = "{" + string.Join(",", sources.Select(pair => Quote(pair.Key) + ":" + Quote(pair.Value))) + "}";
        var artifactJson = "{" + string.Join(",", artifacts.Select(pair => Quote(pair.Key) + ":" + Quote(pair.Value))) + "}";
        var json = "{\"success\":" + (success ? "true" : "false") + ",\"source_snapshot_unchanged\":" + (sourceStable ? "true" : "false") + ",\"source_changes\":[" + string.Join(",", sourceChanges.Select(Quote)) + "],\"finished_at\":" + Quote(DateTime.UtcNow.ToString("o")) + ",\"build_id\":" + Quote(id) +
            ",\"source_hashes\":" + sourceJson + ",\"artifact_hashes\":" + artifactJson + ",\"output\":" + Quote(output) + ",\"output_sha256\":" + Quote(built && File.Exists(output) ? Hash(File.ReadAllBytes(output)) : "") +
            ",\"platform\":" + Quote(target.ToString()) + ",\"unity_version\":" + Quote(Application.unityVersion) + ",\"size_bytes\":" + report.summary.totalSize +
            ",\"duration_seconds\":" + report.summary.totalTime.TotalSeconds.ToString("F3", System.Globalization.CultureInfo.InvariantCulture) + ",\"errors\":" + report.summary.totalErrors + ",\"warnings\":" + report.summary.totalWarnings + "}";
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(record))); File.WriteAllText(record, json, new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(output), "build-status.json"), json, new UTF8Encoding(false));
        if (!sourceStable) throw new Exception("Unity source/settings changed during build: " + string.Join(", ", sourceChanges) + "; rerun after imports and target settings settle.");
        if (!success) throw new Exception("Unity build failed: " + report.summary.result);
        Debug.Log("EEG_BUILD_SUCCEEDED " + id + " " + output);
    }
}
