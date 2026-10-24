using System;
using System.IO;
using UnityEngine;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;
using EEGMovement.Unity;

public static class AcquisitionPresentationValidation
{
    public static void Validate()
    {
        var scene=EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Additive);
        var root=new GameObject("White collection validation");SceneManager.MoveGameObjectToScene(root,scene);
        var camObject=new GameObject("Preview camera");camObject.transform.SetParent(root.transform,false);
        var camera=camObject.AddComponent<Camera>();camera.enabled=true;camera.cameraType=CameraType.Game;camera.scene=scene;
        camera.fieldOfView=75;camera.aspect=960f/540;camera.cullingMask=~0;camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.black;
        var presentation=root.AddComponent<AcquisitionPresentation>();
        var target=new RenderTexture(960,540,16);var pixels=new Texture2D(960,540,TextureFormat.RGB24,false);
        try
        {
            camera.targetTexture=target;target.Create();
            presentation.Show(camera,"想象向前移动");Canvas.ForceUpdateCanvases();
            var label=root.GetComponentInChildren<UnityEngine.UI.Text>();
            Debug.Log("EEG_WHITE_RENDER_SETUP active="+label.gameObject.activeInHierarchy+" font="+label.font+" vertices="+label.cachedTextGenerator.vertexCount+" canvasScene="+label.gameObject.scene.name+" targetScene="+scene.name);
            camera.Render();
            var previous=RenderTexture.active;RenderTexture.active=target;pixels.ReadPixels(new Rect(0,0,960,540),0,0);pixels.Apply();RenderTexture.active=previous;
            var values=pixels.GetPixels32();int white=0,dark=0,left=960,right=0,bottom=540,top=0;
            for(int y=0;y<540;y++)for(int x=0;x<960;x++)
            {
                var p=values[y*960+x];if(p.r>245&&p.g>245&&p.b>245)white++;
                if(p.r<160&&p.g<160&&p.b<160){dark++;left=Math.Min(left,x);right=Math.Max(right,x);bottom=Math.Min(bottom,y);top=Math.Max(top,y);}
            }
            if(white<values.Length*.95||dark<100||left<240||right>720||bottom<190||top>350)
                throw new Exception("White instruction render failed: white="+white+" dark="+dark+" bounds="+left+","+bottom+","+right+","+top);
            presentation.Hide();
            if(camera.cullingMask!=~0||camera.clearFlags!=CameraClearFlags.SolidColor||camera.backgroundColor!=Color.black)
                throw new Exception("Acquisition camera settings did not restore");
            string directory=Path.GetFullPath(Path.Combine(Application.dataPath,"../../../outputs"));Directory.CreateDirectory(directory);
            File.WriteAllBytes(Path.Combine(directory,"white_collection_preview.png"),pixels.EncodeToPNG());
            File.WriteAllText(Path.Combine(directory,"white_collection_render_validation.json"),"{\"status\":\"pass\",\"white_pixels\":"+white+",\"total_pixels\":"+values.Length+",\"dark_text_pixels\":"+dark+",\"camera_restored\":true,\"scope\":\"Unity rendered view; headset interaction separate\"}");
            Debug.Log("EEG_WHITE_COLLECTION_VALIDATED white_background=true centered_text=true camera_restored=true");
        }
        finally
        {
            presentation.Hide();camera.targetTexture=null;
            UnityEngine.Object.DestroyImmediate(root);target.Release();UnityEngine.Object.DestroyImmediate(target);UnityEngine.Object.DestroyImmediate(pixels);
            EditorSceneManager.CloseScene(scene,true);
        }
    }
}
