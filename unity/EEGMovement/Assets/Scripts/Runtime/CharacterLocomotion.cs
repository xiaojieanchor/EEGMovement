using UnityEngine;

namespace EEGMovement.Unity
{
    // Visual animation follows measured applied motion. Root motion never drives the receiver.
    [RequireComponent(typeof(Animator))]
    public sealed class CharacterLocomotion : MonoBehaviour
    {
        public EEGAvatarController controller;
        Animator animator;
        bool moving,turning;
        string lastMode;
        readonly Vector3[] planted=new Vector3[2];
        readonly Quaternion[] plantedRotation=new Quaternion[2];
        readonly bool[] contact=new bool[2];
        void Awake()
        {
            animator=GetComponent<Animator>();animator.applyRootMotion=false;
            animator.cullingMode=AnimatorCullingMode.AlwaysAnimate;
            Debug.Log("EEG_AVATAR_READY name=Kaguya author=水色の描 humanoid="+animator.isHuman+" root_motion=false");
        }
        void Update()
        {
            var state=controller?controller.State:null;
            float speed=state==null?0:(float)state.actual_speed;
            float yaw=state==null?0:(float)state.actual_yaw_rate;
            moving=Mathf.Abs(speed)>(moving?.015f:.03f);
            turning=!moving&&Mathf.Abs(yaw)>(turning?2:5);
            animator.SetBool("moving",moving);animator.SetBool("turning",turning);
            float gait=moving?Mathf.Sign(speed):turning?Mathf.Sign(yaw):1;
            float rate=moving?Mathf.Clamp(Mathf.Abs(speed)/.65f,.16f,1.8f):turning?Mathf.Clamp(Mathf.Abs(yaw)/65,.2f,1.4f):1;
            animator.SetFloat("gait",gait,.1f,Time.deltaTime);animator.SetFloat("rate",rate,.12f,Time.deltaTime);
            string mode=moving?(speed<0?"backward":"walk"):turning?(yaw<0?"turn_left":"turn_right"):"idle";
            if(mode!=lastMode){lastMode=mode;Debug.Log("EEG_AVATAR_MOTION "+mode+" measured_speed="+speed.ToString("F2")+" measured_yaw="+yaw.ToString("F1"));}
        }
        void OnAnimatorIK(int layer)
        {
            if(!animator||!animator.isHuman)return;
            for(int i=0;i<2;i++)
            {
                var goal=i==0?AvatarIKGoal.LeftFoot:AvatarIKGoal.RightFoot;
                Vector3 foot=animator.GetIKPosition(goal);Quaternion rotation=animator.GetIKRotation(goal);
                RaycastHit hit;
                if(!Physics.Raycast(foot+Vector3.up*.35f,Vector3.down,out hit,.9f,~(1<<8),QueryTriggerInteraction.Ignore))
                {contact[i]=false;animator.SetIKPositionWeight(goal,0);animator.SetIKRotationWeight(goal,0);continue;}
                float sole=i==0?animator.leftFeetBottomHeight:animator.rightFeetBottomHeight;
                float clearance=foot.y-hit.point.y-sole;
                float weight=1-Mathf.SmoothStep(0,1,Mathf.Clamp01((clearance-.015f)/.10f));
                if(weight<.15f)contact[i]=false;
                if(weight>.7f&&(!contact[i]||Vector3.Distance(planted[i],foot)>.27f))
                {contact[i]=true;planted[i]=hit.point+hit.normal*sole;plantedRotation[i]=Quaternion.FromToRotation(rotation*Vector3.up,hit.normal)*rotation;}
                Vector3 target=contact[i]?planted[i]:hit.point+hit.normal*sole;
                animator.SetIKPositionWeight(goal,weight*.95f);animator.SetIKRotationWeight(goal,weight*.85f);
                animator.SetIKPosition(goal,target);animator.SetIKRotation(goal,contact[i]?plantedRotation[i]:rotation);
            }
        }
    }
}
