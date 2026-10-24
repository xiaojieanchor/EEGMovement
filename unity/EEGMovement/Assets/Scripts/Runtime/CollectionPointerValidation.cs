using UnityEngine;using UnityEngine.UI;
namespace EEGMovement.Unity
{
    // Called only by the explicitly requested isolated self-paced CLI test.
    public static class CollectionPointerValidation
    {
        public static bool Verify()
        {
            var root=new GameObject("Isolated collection pointer renderer test");
            var camObject=new GameObject("Offscreen test camera");camObject.transform.SetParent(root.transform);var camera=camObject.AddComponent<Camera>();camera.enabled=false;camera.orthographic=true;camera.orthographicSize=.5f;camera.transform.position=new Vector3(0,0,-2);camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.white;camera.cullingMask=1<<29;
            var go=new GameObject("Opaque high-sort collection canvas",typeof(RectTransform),typeof(Canvas));go.transform.SetParent(root.transform);go.layer=29;var canvas=go.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=camera;canvas.sortingOrder=32750;go.GetComponent<RectTransform>().sizeDelta=Vector2.one;
            var bg=new GameObject("White background",typeof(RectTransform),typeof(Image));bg.transform.SetParent(go.transform,false);bg.layer=29;bg.GetComponent<RectTransform>().sizeDelta=new Vector2(4,4);bg.GetComponent<Image>().color=Color.white;
            var lineObject=new GameObject("Test ray");lineObject.transform.SetParent(root.transform);var line=lineObject.AddComponent<LineRenderer>();line.enabled=false;
            var ringObject=new GameObject("Actual pointer ring material");ringObject.transform.SetParent(root.transform);var ring=ringObject.AddComponent<LineRenderer>();ring.material=Resources.Load<Material>("WorkspaceLine");ring.positionCount=32;ring.loop=true;ring.startWidth=ring.endWidth=.014f;ring.startColor=ring.endColor=new Color(.02f,.48f,.66f,1);
            PointerSurface.Configure(line,ring,canvas,true);
            var target=new RenderTexture(192,192,24);target.Create();camera.targetTexture=target;var pixels=new Texture2D(192,192,TextureFormat.RGB24,false);bool okay=true;float previous=-999;
            for(int j=0;j<31;j++)
            {
                float x=-.9f+j*.06f;Vector3 point;okay&=PointerSurface.Intersect(canvas,new Ray(camera.transform.position,new Vector3(x,0,2)),true,out point)&&point.x>previous;previous=point.x;
            }
            for(int j=0;j<3;j++)
            {
                Vector3 point;okay&=PointerSurface.Intersect(canvas,new Ray(camera.transform.position,new Vector3((j-1)*.3f,0,2)),true,out point);
                for(int n=0;n<32;n++){float a=n*Mathf.PI*2/32;ring.SetPosition(n,point+new Vector3(Mathf.Cos(a)*.04f,Mathf.Sin(a)*.04f,-.003f));}
                Canvas.ForceUpdateCanvases();camera.Render();var old=RenderTexture.active;RenderTexture.active=target;pixels.ReadPixels(new Rect(0,0,192,192),0,0);pixels.Apply();RenderTexture.active=old;int colored=0;foreach(var p in pixels.GetPixels32())if(p.r<150&&p.b>80)colored++;
                okay&=colored>8;Debug.Log("EEG_COLLECTION_POINTER_RENDER point="+j+" colored_pixels="+colored+" order="+ring.sortingOrder+" continuous_plane=true");
            }
            camera.targetTexture=null;target.Release();Object.Destroy(target);Object.Destroy(pixels);Object.Destroy(root);
            return okay;
        }
    }
}
