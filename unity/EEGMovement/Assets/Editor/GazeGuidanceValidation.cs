using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;
using EEGMovement.Unity;

public static class GazeGuidanceValidation
{
    public static void Navigation()
    {
        var owner=new GameObject("Synthetic navigation imagery validation");
        try
        {
            var component=owner.AddComponent<SelfPacedAcquisition>();
            var flags=BindingFlags.Instance|BindingFlags.NonPublic;
            var field=typeof(SelfPacedAcquisition).GetField("currentSession",flags);
            var json=System.IO.File.ReadAllText(Environment.GetEnvironmentVariable("EEGMOVEMENT_NAVIGATION_JSON"));
            field.SetValue(component,JsonUtility.FromJson(json,field.FieldType));
            typeof(SelfPacedAcquisition).GetMethod("BuildSurface",flags).Invoke(component,null);
            typeof(SelfPacedAcquisition).GetProperty("Held").GetSetMethod(true).Invoke(component,new object[]{"FORWARD"});
            var help=(string)typeof(SelfPacedAcquisition).GetMethod("TaskHelp",flags).Invoke(component,null);
            if(!help.Contains("想象自己向前行走")||!help.Contains("眼动仅作辅助")||help.Contains("无需肢体想象"))throw new Exception("Navigation imagery instruction was not consumed: "+help);
            var surface=(GameObject)typeof(SelfPacedAcquisition).GetField("surface",flags).GetValue(component);
            if(surface.GetComponentsInChildren<HoldLabelPress>(true).Length!=5)throw new Exception("Navigation label set was not consumed");
            var output=Environment.GetEnvironmentVariable("EEGMOVEMENT_NAVIGATION_VALIDATION");
            System.IO.File.WriteAllText(output,"{\"success\":true,\"actual_json_utility\":true,\"motor_imagery_primary\":true,\"gaze_auxiliary\":true,\"real_device_test\":false}");
            Debug.Log("EEG_NAVIGATION_IMAGERY_VALIDATED: actual JsonUtility and client task text, synthetic only.");
        }
        finally {UnityEngine.Object.DestroyImmediate(owner);}
    }
    public static void Validate()
    {
        var owner=new GameObject("Synthetic gaze guidance validation");
        try
        {
            var component=owner.AddComponent<SelfPacedAcquisition>();
            var flags=BindingFlags.Instance|BindingFlags.NonPublic;
            var field=typeof(SelfPacedAcquisition).GetField("currentSession",flags);
            if(field==null)throw new Exception("Actual self-paced client does not consume server gaze session guidance");
            const string json="{\"active\":true,\"session_id\":\"synthetic\",\"recording\":\"fixture\",\"experiment_mode\":\"gaze_only\",\"task_binding_version\":\"gaze_navigation_v2\",\"labels\":[\"FORWARD\",\"BACKWARD\",\"LEFT\",\"RIGHT\",\"REST\"],\"task_instructions_list\":[{\"label\":\"FORWARD\",\"gaze_instruction\":\"UP\",\"instruction\":\"向上看；无需肢体想象，可正常操作手柄。\"},{\"label\":\"BACKWARD\",\"gaze_instruction\":\"DOWN\",\"instruction\":\"向下看\"}]}";
            field.SetValue(component,JsonUtility.FromJson(json,field.FieldType));
            typeof(SelfPacedAcquisition).GetMethod("BuildSurface",flags).Invoke(component,null);
            var surface=(GameObject)typeof(SelfPacedAcquisition).GetField("surface",flags).GetValue(component);
            var text=string.Join("\n",surface.GetComponentsInChildren<Text>(true).Select(v=>v.text));
            if(!text.Contains("向上看")||!text.Contains("向下看")||!text.Contains("无需肢体想象")||text.Contains("按住想象"))throw new Exception("Actual gaze surface still shows old/missing direction guidance: "+text);
            if(surface.GetComponentsInChildren<HoldLabelPress>(true).Length!=5)throw new Exception("Server label set was not applied to actual buttons");
            typeof(SelfPacedAcquisition).GetProperty("Active").GetSetMethod(true).Invoke(component,new object[]{true});
            if(component.Press("FORWARD_LEFT",99))throw new Exception("Unsupported combined direction was accepted");
            var output=Environment.GetEnvironmentVariable("EEGMOVEMENT_GAZE_VALIDATION");
            if(!string.IsNullOrEmpty(output))System.IO.File.WriteAllText(output,"{\"success\":true,\"actual_json_utility\":true,\"gaze_buttons\":5,\"invalid_direction_rejected\":true,\"real_device_test\":false}");
            Debug.Log("EEG_GAZE_GUIDANCE_VALIDATED: real JsonUtility and UGUI consumption, explicit UP/DOWN, no limb requirement, 5 labels, illegal direction rejected; synthetic only.");
        }
        finally {UnityEngine.Object.DestroyImmediate(owner);}
    }
}
