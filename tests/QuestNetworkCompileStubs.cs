// Syntax-check boundary only. This does not stand in for Unity/Android execution.
using System;
using System.Collections;
using System.Web.Script.Serialization;
using EEGMovement.Core;
namespace UnityEngine {
    public class Object {public static implicit operator bool(Object o){return o!=null;}}
    public class MonoBehaviour:Object {public T GetComponent<T>() where T:new(){return new T();} public object StartCoroutine(IEnumerator i){return null;}}
    public class AndroidJavaObject:IDisposable {public AndroidJavaObject(string name,params object[] args){} public void Call(string method,params object[] args){} public T Call<T>(string method,params object[] args){return default(T);} public T GetStatic<T>(string name){return default(T);} public void Dispose(){}}
    public class AndroidJavaClass:AndroidJavaObject {public AndroidJavaClass(string name):base(name){}}
    public class AndroidJavaProxy {public AndroidJavaProxy(string name){}}
    public static class Application {public static string persistentDataPath="";}
    public static class Resources {public static T Load<T>(string path){return default(T);}}
    public sealed class TextAsset {public string text;}
    public static class JsonUtility {public static T FromJson<T>(string text){return new JavaScriptSerializer().Deserialize<T>(text);}public static string ToJson(object data){return new JavaScriptSerializer().Serialize(data);}}
    public static class Debug {public static void Log(object o){}}
    public static class Time {public static float unscaledTime;}
    public class WaitForSecondsRealtime {public WaitForSecondsRealtime(float seconds){}}
}
namespace EEGMovement.Unity {
    public class EEGAvatarController {public void EmergencyStop(){}public void TransportChanged(){}}
    public class UnityJson:IJsonCodec {public T Decode<T>(string s){return UnityEngine.JsonUtility.FromJson<T>(s);}public string Encode(object o){return UnityEngine.JsonUtility.ToJson(o);}}
}
