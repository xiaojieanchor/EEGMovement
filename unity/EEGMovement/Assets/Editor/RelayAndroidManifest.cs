using System.IO;
using System.Xml;
using UnityEditor.Android;

public sealed class RelayAndroidManifest:IPostGenerateGradleAndroidProject {
    public int callbackOrder {get{return 1000;}}
    public void OnPostGenerateGradleAndroidProject(string path){
        string manifest=Path.Combine(path,"src/main/AndroidManifest.xml");var doc=new XmlDocument();doc.Load(manifest);var root=doc.DocumentElement;
        const string ns="http://schemas.android.com/apk/res/android";
        foreach(string name in new[]{"android.permission.REQUEST_INSTALL_PACKAGES","android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION"}){var item=doc.CreateElement("uses-permission");item.SetAttribute("name",ns,name);root.AppendChild(item);}
        var application=doc.SelectSingleNode("/manifest/application");var receiver=doc.CreateElement("receiver");receiver.SetAttribute("name",ns,"org.eegmovement.relay.UpdateBridge$InstallResult");receiver.SetAttribute("exported",ns,"false");application.AppendChild(receiver);doc.Save(manifest);
        File.AppendAllText(Path.Combine(path,"build.gradle"),"\ntasks.withType(JavaCompile).configureEach { options.encoding = 'UTF-8' }\n");
    }
}
