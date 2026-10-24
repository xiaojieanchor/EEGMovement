using UnityEngine;
namespace EEGMovement.Unity
{
    public static class PointerSurface
    {
        public static bool Intersect(Canvas canvas,Ray ray,bool collection,out Vector3 point)
        {
            point=ray.GetPoint(3);float distance;
            if(!canvas||!new Plane(canvas.transform.forward,canvas.transform.position).Raycast(ray,out distance)||distance<=0||distance>=5)return false;
            var hit=ray.GetPoint(distance);var local=canvas.transform.InverseTransformPoint(hit);
            if(!collection&&!canvas.GetComponent<RectTransform>().rect.Contains(new Vector2(local.x,local.y)))return false;
            point=hit;return true;
        }
        public static void Configure(LineRenderer line,LineRenderer ring,Canvas canvas,bool collection)
        {
            line.gameObject.layer=ring.gameObject.layer=canvas.gameObject.layer;
            line.sortingOrder=collection?32760:100;ring.sortingOrder=collection?32761:110;
        }
    }
}
