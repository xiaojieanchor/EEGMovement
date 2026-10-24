using UnityEngine;
namespace EEGMovement.Unity {
    public sealed class FollowCamera : MonoBehaviour
    {
        public Transform target;
        public EEGAvatarController controller;
        Vector3 velocity;
        public void Recenter() { velocity = Vector3.zero; if (target) transform.position = target.position + new Vector3(5.5f, 5.2f, -7); }
        void LateUpdate()
        {
            if (!target) return;
            var desired = target.position + new Vector3(5.5f, 5.2f, -7);
            transform.position = controller && controller.ReduceMotion ? desired : Vector3.SmoothDamp(transform.position, desired, ref velocity, .22f);
            transform.LookAt(target.position + Vector3.up * .95f);
        }
    }
}
