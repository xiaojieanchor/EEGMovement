using UnityEngine;
namespace EEGMovement.Unity
{
    public sealed class AppliedDirectionFeedback : MonoBehaviour
    {
        public EEGAvatarController controller;
        LineRenderer ring, arc, arrow;
        LineRenderer Line(string name, Color color, float width, int points)
        {
            var go = new GameObject(name); go.transform.SetParent(transform, false); var line = go.AddComponent<LineRenderer>();
            line.useWorldSpace = false; line.positionCount = points; line.startWidth = line.endWidth = width;
            var preserved = Resources.Load<Material>("WorkspaceLine");
            line.material = preserved ? preserved : new Material(Shader.Find("Sprites/Default")); line.startColor = line.endColor = color; return line;
        }
        void Start()
        {
            ring = Line("Applied pose reference ring", new Color(.32f, .42f, .46f, .65f), .018f, 65);
            for (int i = 0; i <= 64; i++) { float a = i * Mathf.PI * 2 / 64; ring.SetPosition(i, new Vector3(Mathf.Sin(a) * .76f, .045f, Mathf.Cos(a) * .76f)); }
            arc = Line("Actual applied motion arc", new Color(0, .42f, .52f), .065f, 25);
            arrow = Line("Actual travel direction", new Color(0, .42f, .52f), .045f, 3);
        }
        void LateUpdate()
        {
            if (!arc) return;
            var s = controller.State; bool fresh = s != null && s.paired && s.command_age_ms >= 0 && s.command_age_ms <= 180;
            bool applied = fresh && !controller.Workspace.EmergencyLatched && (System.Math.Abs(s.actual_speed) > .005 || System.Math.Abs(s.actual_yaw_rate) > .1);
            arc.enabled = arrow.enabled = applied;
            if (!applied) return;
            float angle = s.actual_speed < -.005 ? 180 : System.Math.Abs(s.actual_yaw_rate) > .1 ? (s.actual_yaw_rate < 0 ? -70 : 70) : 0;
            for (int i = 0; i < 25; i++) { float a = (angle - 50 + i * 100f / 24) * Mathf.Deg2Rad; arc.SetPosition(i, new Vector3(Mathf.Sin(a) * .76f, .051f, Mathf.Cos(a) * .76f)); }
            var rotation = Quaternion.Euler(0, angle, 0); arrow.SetPosition(0, rotation * new Vector3(-.13f, .052f, .89f)); arrow.SetPosition(1, rotation * new Vector3(0, .052f, 1.08f)); arrow.SetPosition(2, rotation * new Vector3(.13f, .052f, .89f));
        }
    }
}
