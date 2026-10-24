using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace EEGMovement.Unity
{
    // A stable white field with a persistent instruction and separate timing.
    [DefaultExecutionOrder(10000)]
    public sealed class AcquisitionPresentation : MonoBehaviour
    {
        public GuidedAcquisition Owner;
        const int PresentationLayer = 30;
        struct Saved { public int mask; public CameraClearFlags flags; public Color background; }
        readonly Dictionary<Camera,Saved> cameras = new Dictionary<Camera,Saved>();
        GameObject canvasObject;
        Text instruction,phaseTitle,countdown,detail,help;
        Image progressTrack,progressFill;
        Camera source;
        bool showing;
        void OnEnable() { Camera.onPreCull += BeforeCamera; }
        void BeforeCamera(Camera camera)
        {
            if (!showing || !camera) return;
            if (!cameras.ContainsKey(camera)) cameras.Add(camera,new Saved { mask=camera.cullingMask, flags=camera.clearFlags, background=camera.backgroundColor });
            camera.cullingMask=1<<PresentationLayer;
            camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.white;
        }
        Text MakeText(Transform parent,string name,int size,float y,float height)
        {
            var go=new GameObject(name,typeof(RectTransform),typeof(Text));go.layer=PresentationLayer;go.transform.SetParent(parent,false);
            var rect=go.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(1400,height);rect.anchoredPosition=new Vector2(0,y);
            var text=go.GetComponent<Text>();text.font=Resources.Load<Font>("Fonts/NotoSansCJKsc-Regular");
            text.fontSize=size;text.color=new Color(.07f,.07f,.07f,1);text.alignment=TextAnchor.MiddleCenter;
            text.raycastTarget=false;text.supportRichText=false;text.resizeTextForBestFit=false;return text;
        }
        public void Show(Camera camera,string value,string stage="",string timer="",string status="",string controls="",float progress=-1)
        {
            if (!camera) return;
            if (!canvasObject)
            {
                canvasObject=new GameObject("White collection · persistent cue and countdown",typeof(RectTransform),typeof(Canvas));
                canvasObject.layer=PresentationLayer;
                var canvas=canvasObject.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.sortingOrder=32760;
                var scaler=canvasObject.AddComponent<CanvasScaler>();scaler.dynamicPixelsPerUnit=2;
                var rect=canvasObject.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(1500,900);
                phaseTitle=MakeText(rect,"Phase and trial",36,320,80);
                instruction=MakeText(rect,"Persistent instruction",76,170,160);
                countdown=MakeText(rect,"Countdown",64,0,140);
                detail=MakeText(rect,"Progress and status",34,-230,130);
                help=MakeText(rect,"Controller help",30,-350,80);
                var track=new GameObject("Session progress",typeof(RectTransform),typeof(Image));track.layer=PresentationLayer;track.transform.SetParent(rect,false);
                var tr=track.GetComponent<RectTransform>();tr.sizeDelta=new Vector2(1050,10);tr.anchoredPosition=new Vector2(0,-125);
                progressTrack=track.GetComponent<Image>();progressTrack.color=new Color(.86f,.90f,.88f);progressTrack.raycastTarget=false;
                var fill=new GameObject("Accepted progress",typeof(RectTransform),typeof(Image));fill.layer=PresentationLayer;fill.transform.SetParent(tr,false);
                var fr=fill.GetComponent<RectTransform>();fr.anchorMin=Vector2.zero;fr.anchorMax=Vector2.one;fr.offsetMin=fr.offsetMax=Vector2.zero;
                progressFill=fill.GetComponent<Image>();progressFill.color=new Color(.13f,.40f,.29f);progressFill.raycastTarget=false;
            }
            if (source!=camera)
            {
                source=camera;canvasObject.transform.SetParent(camera.transform,false);
                canvasObject.transform.localPosition=Vector3.forward*1.6f;
                canvasObject.transform.localRotation=Quaternion.identity;canvasObject.transform.localScale=Vector3.one*.001f;
                canvasObject.GetComponent<Canvas>().worldCamera=camera;
            }
            instruction.text=string.IsNullOrEmpty(value)?"等待下一条指令":value;
            phaseTitle.text=stage;countdown.text=timer;detail.text=status;help.text=controls;
            progressTrack.gameObject.SetActive(progress>=0);
            progressFill.rectTransform.anchorMax=new Vector2(Mathf.Clamp01(progress),1);
            canvasObject.SetActive(true);showing=true;BeforeCamera(camera);
        }
        public void Hide()
        {
            showing=false;if(canvasObject)canvasObject.SetActive(false);
            foreach(var pair in cameras)if(pair.Key){pair.Key.cullingMask=pair.Value.mask;pair.Key.clearFlags=pair.Value.flags;pair.Key.backgroundColor=pair.Value.background;}
            cameras.Clear();
        }
        void LateUpdate()
        {
            if(!Owner)return;
            if(!Owner.Immersive){Hide();return;}
            Camera camera=null;
            var rig=FindFirstObjectByType<SpatialWorkspaceRig>();
            if(rig&&rig.Active&&rig.Head)camera=rig.Head.GetComponent<Camera>();
            if(!camera)camera=Camera.main;
            Show(camera,Owner.Prompt,Owner.PhaseTitle+" · "+Owner.CurrentTrial+" / "+Owner.TrialCount,
                Owner.TimingText,Owner.Status,"左手 X 暂停 / 继续    ·    右手 A 结束并保存",Owner.Progress);
        }
        void OnDisable(){Camera.onPreCull-=BeforeCamera;Hide();}
        void OnDestroy(){if(canvasObject){if(Application.isPlaying)Destroy(canvasObject);else DestroyImmediate(canvasObject);}}
    }
}
