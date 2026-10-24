using System;using System.Collections;using System.IO;using UnityEngine;using UnityEngine.EventSystems;
namespace EEGMovement.Unity
{
    // Explicit CLI-only fixture validation; never created during normal use.
    public sealed class SelfPacedValidation:MonoBehaviour
    {
        [Serializable]sealed class Result{public bool success,stopped,pointer_rendered_above_white,white_background_blocks_clickthrough,second_pointer_cannot_take_label;public string build_id,recording,status,scope;public int confirmed_events,buttons;public long rendered_frames;}
        string output;
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]static void Enable()
        {var args=Environment.GetCommandLineArgs();for(int i=0;i+1<args.Length;i++)if(args[i]=="--eeg-validate-selfpaced"){var runner=new GameObject("Explicit self-paced validation").AddComponent<SelfPacedValidation>();runner.output=Path.GetFullPath(args[i+1]);return;}}
        IEnumerator Start()
        {
            yield return null;yield return null;var controller=FindFirstObjectByType<EEGAvatarController>();var ui=FindFirstObjectByType<WorkspaceUI>();ui.Show("collection");var a=controller.SelfPaced;a.Begin();
            float deadline=Time.realtimeSinceStartup+12;while(!a.Active&&a.Busy&&Time.realtimeSinceStartup<deadline)yield return null;
            bool pointerOkay=CollectionPointerValidation.Verify();bool okay=a.Active&&pointerOkay,stopped=true,blocks=false,ownerOkay=true;int buttons=0;
            if(a.Active)
            {
                yield return null;var keys=FindObjectsByType<HoldLabelPress>(FindObjectsSortMode.None);buttons=keys.Length;okay&=buttons==9;
                var background=a.InputCanvas.transform.Find("White field").GetComponent<UnityEngine.UI.Image>();blocks=background.raycastTarget;okay&=blocks;
                string[] labels={"FORWARD","LEFT","REST","RIGHT","BACKWARD","FORWARD_LEFT","FORWARD_RIGHT","BACKWARD_LEFT","BACKWARD_RIGHT"};float[] durations={1.1f,1.7f,.8f,1.3f,2.2f,1.2f,.9f,1.4f,1.6f};
                for(int i=0;i<labels.Length&&a.Active;i++)
                {
                    HoldLabelPress key=null;foreach(var candidate in keys)if(candidate.Label==labels[i])key=candidate;if(!key){okay=false;break;}
                    var e=new PointerEventData(EventSystem.current){pointerId=99};key.OnPointerDown(e);okay&=a.Held==labels[i];
                    HoldLabelPress other=null;foreach(var candidate in keys)if(candidate.Label!=labels[i]){other=candidate;break;}
                    var second=new PointerEventData(EventSystem.current){pointerId=100};other.OnPointerDown(second);other.OnPointerUp(second);ownerOkay&=a.Held==labels[i];okay&=ownerOkay;
                    float until=Time.realtimeSinceStartup+durations[i];while(Time.realtimeSinceStartup<until&&a.Active){var s=controller.State;stopped&=s==null||(Math.Abs(s.actual_speed)<.005&&Math.Abs(s.actual_yaw_rate)<.005);yield return null;}
                    key.OnPointerUp(e);okay&=a.Held==null;yield return new WaitForSecondsRealtime(.25f);
                }
                yield return new WaitForEndOfFrame();ScreenCapture.CaptureScreenshot(output+".png");a.Finish();
            }
            deadline=Time.realtimeSinceStartup+10;while(a.Busy&&Time.realtimeSinceStartup<deadline)yield return null;
            var r=new Result{success=okay&&!a.Busy&&a.Saved>=10&&stopped,stopped=stopped,pointer_rendered_above_white=pointerOkay,white_background_blocks_clickthrough=blocks,second_pointer_cannot_take_label=ownerOkay,build_id=controller.Health.build_id,recording=a.Recording,status=a.Status,confirmed_events=a.Saved,buttons=buttons,rendered_frames=controller.Health.rendered_frames,scope="Actual Unity hold handlers, offscreen world-space pointer rendering, continuous plane and HTTP protocol against isolated synthetic fixture; not personal EEG or physical Quest acceptance"};
            Directory.CreateDirectory(Path.GetDirectoryName(output));File.WriteAllText(output,JsonUtility.ToJson(r,true));Debug.Log(r.success?"EEG_SELF_PACED_VALIDATION_PASS":"EEG_SELF_PACED_VALIDATION_FAIL");Application.Quit(r.success?0:2);
        }
    }
}
