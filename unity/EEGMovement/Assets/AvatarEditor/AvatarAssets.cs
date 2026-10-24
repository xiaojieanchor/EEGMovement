using System;
using System.IO;
using System.Linq;
using UnityEngine;
using UnityEditor;
using UniGLTF;
using UnityEditor.Animations;
using UnityEngine.Animations;
using UnityEngine.Playables;

public static class AvatarAssets
{
    static double deadline;
    public const string Prefab = "Assets/Resources/Characters/Kaguya.prefab";
    public const string Source = "Assets/Characters/AnimationSource/UAL1_Standard.fbx";
    public static void Inspect()
    {
        deadline = EditorApplication.timeSinceStartup + 90;
        Directory.CreateDirectory("Assets/Resources/Characters");
        if (!AssetDatabase.LoadAssetAtPath<GameObject>(Prefab))
        {
            VRM.vrmAssetPostprocessor.ImportVrmAndCreatePrefab("G:/Unity/Downloads/EEGMovement-Avatar/Kaguya.vrm", UnityPath.FromUnityPath(Prefab));
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }
        EditorApplication.update += CompleteInspection;
    }
    static void CompleteInspection()
    {
        if (!AssetDatabase.LoadAssetAtPath<GameObject>(Prefab))
        { if (EditorApplication.timeSinceStartup < deadline) return; Debug.LogError("VRM import timeout"); EditorApplication.Exit(1); return; }
        EditorApplication.update -= CompleteInspection;
        try { InspectImported(); EditorApplication.Exit(0); }
        catch (Exception error) { Debug.LogException(error); EditorApplication.Exit(1); }
    }
    static void InspectImported()
    {
        var importer = (ModelImporter)AssetImporter.GetAtPath(Source);
        if (importer.animationType != ModelImporterAnimationType.Human || !importer.importAnimation)
        {
            importer.animationType = ModelImporterAnimationType.Human;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.importAnimation = true;
            importer.SaveAndReimport();
        }
        var character = AssetDatabase.LoadAssetAtPath<GameObject>(Prefab);
        if (!character) throw new Exception("VRM prefab import did not finish");
        var animator = character.GetComponent<Animator>();
        var sourceAvatar = AssetDatabase.LoadAllAssetsAtPath(Source).OfType<Avatar>().FirstOrDefault();
        string[] clips = AssetDatabase.LoadAllAssetsAtPath(Source).OfType<AnimationClip>().Where(c => !c.name.StartsWith("__preview")).Select(c => c.name + " | " + c.length.ToString("F3") + "s | human=" + c.isHumanMotion).ToArray();
        File.WriteAllText("../../outputs/avatar_import_inventory.txt", "Target humanoid=" + (animator && animator.avatar && animator.avatar.isHuman) + "\nSource humanoid=" + (sourceAvatar && sourceAvatar.isHuman) + "\n" + string.Join("\n", clips));
        if (!animator || !animator.avatar || !animator.avatar.isHuman || !sourceAvatar || !sourceAvatar.isHuman) throw new Exception("Valid humanoid avatars required for retargeting");
        Debug.Log("EEG_AVATAR_IMPORTED humanoid=true clips=" + clips.Length);
        PrepareMotions();
        RenderValidation();
        AssetDatabase.SaveAssets();
    }
    static void PrepareMotions()
    {
        Directory.CreateDirectory("Assets/Resources/Motions");
        var importer=(ModelImporter)AssetImporter.GetAtPath(Source);
        var imported=importer.defaultClipAnimations;
        foreach(var clip in imported)
        { clip.loopTime=clip.name.EndsWith("Loop");clip.loopPose=clip.loopTime;clip.lockRootRotation=true;clip.lockRootHeightY=true;clip.lockRootPositionXZ=true; }
        importer.clipAnimations=imported;importer.SaveAndReimport();
        var sourceClips=AssetDatabase.LoadAllAssetsAtPath(Source).OfType<AnimationClip>().Where(c=>!c.name.StartsWith("__preview")).ToArray();
        AnimationClip Copy(string suffix,string name)
        {
            var original=sourceClips.Single(c=>c.name.EndsWith("|"+suffix));
            string path="Assets/Resources/Motions/"+name+".anim";
            var clip=AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if(!clip){clip=new AnimationClip();AssetDatabase.CreateAsset(clip,path);}
            EditorUtility.CopySerialized(original,clip);clip.name=name;EditorUtility.SetDirty(clip);return clip;
        }
        var idle=Copy("Idle_Loop","KaguyaIdle");var walk=Copy("Walk_Formal_Loop","KaguyaWalk");var step=Copy("Walk_Loop","KaguyaTurnStep");
        const string controllerPath="Assets/Resources/Motions/Kaguya.controller";
        var controller=AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath);
        if(!controller)
        {
            controller=AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            controller.AddParameter("moving",AnimatorControllerParameterType.Bool);controller.AddParameter("turning",AnimatorControllerParameterType.Bool);
            controller.AddParameter("gait",AnimatorControllerParameterType.Float);controller.AddParameter("rate",AnimatorControllerParameterType.Float);
            var machine=controller.layers[0].stateMachine;
            var resting=machine.AddState("Idle");resting.motion=idle;machine.defaultState=resting;
            var walking=machine.AddState("Walk");var turning=machine.AddState("Turn with steps");
            var gait=new BlendTree{name="Forward / backward steps",blendType=BlendTreeType.Simple1D,blendParameter="gait",useAutomaticThresholds=false};AssetDatabase.AddObjectToAsset(gait,controller);
            gait.children=new[]{new ChildMotion{motion=walk,threshold=-1,timeScale=-1},new ChildMotion{motion=walk,threshold=1,timeScale=1}};walking.motion=gait;
            var turn=new BlendTree{name="Left / right turn steps",blendType=BlendTreeType.Simple1D,blendParameter="gait",useAutomaticThresholds=false};AssetDatabase.AddObjectToAsset(turn,controller);
            turn.children=new[]{new ChildMotion{motion=step,threshold=-1,mirror=true,timeScale=1},new ChildMotion{motion=step,threshold=1,timeScale=1}};turning.motion=turn;
            foreach(var state in new[]{walking,turning}){state.speedParameter="rate";state.speedParameterActive=true;}
            void Transition(AnimatorState from,AnimatorState to,params (string,bool)[] conditions)
            {var t=from.AddTransition(to);t.hasExitTime=false;t.duration=.18f;t.hasFixedDuration=true;foreach(var c in conditions)t.AddCondition(c.Item2?AnimatorConditionMode.If:AnimatorConditionMode.IfNot,0,c.Item1);}
            Transition(resting,walking,("moving",true));Transition(resting,turning,("moving",false),("turning",true));
            Transition(walking,resting,("moving",false),("turning",false));Transition(walking,turning,("moving",false),("turning",true));
            Transition(turning,walking,("moving",true));Transition(turning,resting,("moving",false),("turning",false));
            var layers=controller.layers;layers[0].iKPass=true;controller.layers=layers;
        }
        var instance=PrefabUtility.LoadPrefabContents(Prefab);var animator=instance.GetComponent<Animator>();
        animator.runtimeAnimatorController=controller;animator.applyRootMotion=false;animator.cullingMode=AnimatorCullingMode.AlwaysAnimate;
        PrefabUtility.SaveAsPrefabAsset(instance,Prefab);PrefabUtility.UnloadPrefabContents(instance);
        AssetDatabase.SaveAssets();
    }
    static void RenderValidation()
    {
        var scene=UnityEditor.SceneManagement.EditorSceneManager.NewPreviewScene();
        var model=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(Prefab));UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(model,scene);
        var animator=model.GetComponent<Animator>();animator.runtimeAnimatorController=null;
        foreach(var renderer in model.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {renderer.enabled=true;renderer.forceRenderingOff=false;renderer.updateWhenOffscreen=true;Debug.Log("EEG_AVATAR_RENDER "+renderer.name+" bounds="+renderer.bounds+" vertices="+renderer.sharedMesh.vertexCount);}
        var light=new GameObject("Preview light");UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(light,scene);light.transform.rotation=Quaternion.Euler(35,-35,0);light.AddComponent<Light>().type=LightType.Directional;
        var cameraObject=new GameObject("Preview camera");UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(cameraObject,scene);var camera=cameraObject.AddComponent<Camera>();camera.stereoTargetEye=StereoTargetEyeMask.None;camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color(.16f,.22f,.27f);camera.transform.position=new Vector3(0,1.0f,3.25f);camera.transform.LookAt(new Vector3(0,.85f,0));
        camera.scene=scene;camera.cameraType=CameraType.Preview;
        var target=new RenderTexture(1024,1024,24);target.Create();camera.targetTexture=target;
        var clip=AssetDatabase.LoadAssetAtPath<AnimationClip>("Assets/Resources/Motions/KaguyaWalk.anim");
        var graph=PlayableGraph.Create("Retarget verification");graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);var playable=AnimationClipPlayable.Create(graph,clip);var output=AnimationPlayableOutput.Create(graph,"Character",animator);output.SetSourcePlayable(playable);graph.Play();
        var skins=model.GetComponentsInChildren<SkinnedMeshRenderer>();var baked=new Mesh[skins.Length];
        for(int n=0;n<skins.Length;n++)
        {
            var sample=new GameObject("CPU skin sample");sample.transform.SetParent(skins[n].transform,false);var filter=sample.AddComponent<MeshFilter>();var renderer=sample.AddComponent<MeshRenderer>();renderer.sharedMaterials=skins[n].sharedMaterials;
            baked[n]=new Mesh();baked[n].MarkDynamic();filter.sharedMesh=baked[n];skins[n].enabled=false;
        }
        var trace=new System.Text.StringBuilder("frame,left_x,left_y,left_z,right_x,right_y,right_z\n");
        string folder="../../outputs/avatar-preview";Directory.CreateDirectory(folder);
        for(int i=0;i<32;i++)
        {
            playable.SetTime(clip.length*i/32);graph.Evaluate(clip.length/32);
            var left=animator.GetBoneTransform(HumanBodyBones.LeftFoot).position;var right=animator.GetBoneTransform(HumanBodyBones.RightFoot).position;
            trace.AppendFormat(System.Globalization.CultureInfo.InvariantCulture,"{0},{1:F4},{2:F4},{3:F4},{4:F4},{5:F4},{6:F4}\n",i,left.x,left.y,left.z,right.x,right.y,right.z);
            for(int n=0;n<skins.Length;n++){skins[n].BakeMesh(baked[n]);baked[n].UploadMeshData(false);}
            camera.Render();var previous=RenderTexture.active;RenderTexture.active=target;var pixels=new Texture2D(1024,1024,TextureFormat.RGB24,false);pixels.ReadPixels(new Rect(0,0,1024,1024),0,0);pixels.Apply();File.WriteAllBytes(folder+"/walk-"+i.ToString("D3")+".png",pixels.EncodeToPNG());RenderTexture.active=previous;UnityEngine.Object.DestroyImmediate(pixels);
        }
        File.WriteAllText(folder+"/pose-trace.csv",trace.ToString());
        graph.Destroy();camera.targetTexture=null;target.Release();UnityEngine.Object.DestroyImmediate(target);foreach(var mesh in baked)UnityEngine.Object.DestroyImmediate(mesh);UnityEditor.SceneManagement.EditorSceneManager.ClosePreviewScene(scene);
    }
}
