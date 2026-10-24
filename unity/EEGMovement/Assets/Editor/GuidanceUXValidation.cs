using System;
using System.IO;
using UnityEditor;
using UnityEngine;
using EEGMovement.Unity;

// Actual Unity rendering of the same presentation component shipped to Quest.
// Generated display states only: no device, collection, model or motion request.
public static class GuidanceUXValidation
{
    public static void BuildAndroid(){Validate();BuildSandbox.Android();}
    public static void Validate()
    {
        string root=Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(),"../../outputs/calibration-v3-20261018/unity-render"));
        Directory.CreateDirectory(root);
        var go=new GameObject("Isolated guidance rendering validation");
        var cameraGo=new GameObject("Isolated validation camera");var camera=cameraGo.AddComponent<Camera>();
        camera.transform.position=new Vector3(10000,10000,10000);camera.fieldOfView=65;
        var target=new RenderTexture(1600,1000,24);camera.targetTexture=target;
        var view=go.AddComponent<AcquisitionPresentation>();
        var old=RenderTexture.active;
        try
        {
            var first=Capture(view,camera,target,root,"guided-task","想象前进并左转","保持当前动作 · 4 / 51","当前剩余 00:08    总计还需 11:42","试次 4 / 51 · 原始脑电与事件记录中","左手 X 暂停 / 继续 · 右手 A 结束并保存",.1f);
            var second=Capture(view,camera,target,root,"guided-countdown","想象前进并左转","保持当前动作 · 4 / 51","当前剩余 00:07    总计还需 11:41","试次 4 / 51 · 原始脑电与事件记录中","左手 X 暂停 / 继续 · 右手 A 结束并保存",.11f);
            int changed=0;for(int i=0;i<first.Length;i++)if(!first[i].Equals(second[i]))changed++;
            if(changed<100)throw new Exception("Countdown rendering did not change");
            Capture(view,camera,target,root,"guided-paused","已暂停","已暂停 · 计时停止","当前剩余 00:07    总计还需 11:41","已确认的数据保留，继续后接着计时","左手 X 继续 · 右手 A 结束并保存",.11f);
            Capture(view,camera,target,root,"quick-calibration","保持静止","快速信号校准 · 1 / 1","本条剩余 00:12","总计还需 00:12 · 已用 00:03","右手 A 取消校准",.2f);
            view.Hide();if(camera.cullingMask!=(~0))throw new Exception("Presentation did not restore camera culling mask");
            File.WriteAllText(Path.Combine(root,"validation.json"),"{\"passed\":true,\"actual_unity_rendering\":true,\"synthetic_display_states\":true,\"physical_quest_acceptance\":false,\"screenshots\":4,\"countdown_changed_pixels\":"+changed+"}");
            Debug.Log("EEG_GUIDANCE_UX_RENDER_PASS");
        }
        finally
        {
            RenderTexture.active=old;camera.targetTexture=null;UnityEngine.Object.DestroyImmediate(go);
            UnityEngine.Object.DestroyImmediate(cameraGo);target.Release();UnityEngine.Object.DestroyImmediate(target);
        }
    }
    static Color32[] Capture(AcquisitionPresentation view,Camera camera,RenderTexture target,string root,string name,string prompt,string stage,string timer,string detail,string help,float progress)
    {
        view.Show(camera,prompt,stage,timer,detail,help,progress);Canvas.ForceUpdateCanvases();camera.Render();RenderTexture.active=target;
        var texture=new Texture2D(target.width,target.height,TextureFormat.RGB24,false);texture.ReadPixels(new Rect(0,0,target.width,target.height),0,0);texture.Apply();
        var pixels=texture.GetPixels32();int dark=0,white=0;
        foreach(var p in pixels){if(p.r<100&&p.g<100&&p.b<100)dark++;if(p.r>240&&p.g>240&&p.b>240)white++;}
        if(dark<2000||white<pixels.Length*.65)throw new Exception("White presentation text/background is missing: "+name);
        File.WriteAllBytes(Path.Combine(root,name+".png"),texture.EncodeToPNG());UnityEngine.Object.DestroyImmediate(texture);return pixels;
    }
}
