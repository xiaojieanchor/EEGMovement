using UnityEngine;

namespace EEGMovement.Unity
{
    public static class SandboxFactory
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void EnsureScene()
        {
            if (Object.FindFirstObjectByType<EEGAvatarController>() == null) Build();
        }
        static Material Material(Color color, bool luminous = false)
        {
            var material = new Material(Shader.Find("Standard")); material.color = color;
            material.SetFloat("_Glossiness", .24f);
            if (luminous) { material.EnableKeyword("_EMISSION"); material.SetColor("_EmissionColor", color * .4f); }
            return material;
        }
        static GameObject Primitive(PrimitiveType type, string name, Vector3 position, Vector3 scale, Material mat, Transform parent, bool solid = false)
        {
            var o = GameObject.CreatePrimitive(type); o.name = name; o.transform.SetParent(parent, false); o.transform.localPosition = position; o.transform.localScale = scale; o.GetComponent<Renderer>().sharedMaterial = mat;
            if (!solid) { var collider = o.GetComponent<Collider>(); if (collider) { if (Application.isPlaying) Object.Destroy(collider); else Object.DestroyImmediate(collider); } }
            return o;
        }
        public static EEGAvatarController Build()
        {
            var world = new GameObject("EEGMovement | local execution laboratory");
            var white = Material(new Color(.88f, .92f, .92f));
            var dark = Material(new Color(.08f, .18f, .21f));
            var teal = Material(new Color(.12f, .64f, .57f), true);
            var pale = Material(new Color(.74f, .85f, .84f));
            var ground = Material(new Color(.81f, .86f, .86f));
            Primitive(PrimitiveType.Plane, "20m instrumented floor", Vector3.zero, Vector3.one * 2, ground, world.transform, true);
            for (int i = -10; i <= 10; i++)
            {
                Primitive(PrimitiveType.Cube, "Grid X " + i, new Vector3(i, .006f, 0), new Vector3(.015f, .008f, 20), pale, world.transform);
                Primitive(PrimitiveType.Cube, "Grid Z " + i, new Vector3(0, .006f, i), new Vector3(20, .008f, .015f), pale, world.transform);
            }
            Primitive(PrimitiveType.Cylinder, "Origin calibration pad", new Vector3(0, .015f, 0), new Vector3(2.6f, .015f, 2.6f), teal, world.transform);
            Primitive(PrimitiveType.Cylinder, "Origin inner pad", new Vector3(0, .032f, 0), new Vector3(2.47f, .008f, 2.47f), white, world.transform);
            for (int i = 0; i < 4; i++)
            {
                Vector3 p = new Vector3(i % 2 == 0 ? -5 : 5, 0, i < 2 ? 5 : -5);
                Primitive(PrimitiveType.Cylinder, "Reference beacon " + i, p + Vector3.up * .6f, new Vector3(.25f, .6f, .25f), dark, world.transform);
                Primitive(PrimitiveType.Sphere, "Beacon light " + i, p + Vector3.up * 1.25f, Vector3.one * .27f, teal, world.transform);
            }
            for (int i = 0; i < 4; i++)
            {
                bool horizontal = i < 2; float side = i % 2 == 0 ? -10 : 10;
                Primitive(PrimitiveType.Cube, "Boundary curb " + i, horizontal ? new Vector3(0, .17f, side) : new Vector3(side, .17f, 0), horizontal ? new Vector3(20, .34f, .15f) : new Vector3(.15f, .34f, 20), dark, world.transform, true);
            }
            var avatar = new GameObject("EEG controlled avatar"); avatar.transform.SetParent(world.transform);
            var characterPrefab=Resources.Load<GameObject>("Characters/Kaguya");
            if(!characterPrefab)throw new System.InvalidOperationException("Import the provided Kaguya humanoid before building the scene.");
            var characterVisual=Object.Instantiate(characterPrefab,avatar.transform);characterVisual.name="かぐや · 水色の描";
            foreach(var child in avatar.GetComponentsInChildren<Transform>(true))child.gameObject.layer=8;
            var character = avatar.AddComponent<CharacterController>(); character.height = 1.9f; character.radius = .25f; character.center = Vector3.up * .95f; character.stepOffset = .15f;
            var controller = avatar.AddComponent<EEGAvatarController>();
            characterVisual.AddComponent<CharacterLocomotion>().controller=controller;
            var lightObject = new GameObject("Soft laboratory light"); lightObject.transform.SetParent(world.transform); lightObject.transform.rotation = Quaternion.Euler(48, -30, 0);
            var light = lightObject.AddComponent<Light>(); light.type = LightType.Directional; light.intensity = 1.05f; light.shadows = LightShadows.Soft;
            RenderSettings.ambientLight = new Color(.65f, .73f, .76f); RenderSettings.fog = true; RenderSettings.fogColor = new Color(.77f, .84f, .85f); RenderSettings.fogMode = FogMode.Linear; RenderSettings.fogStartDistance = 18; RenderSettings.fogEndDistance = 45;
            var camObject = new GameObject("Following desktop camera"); camObject.tag = "MainCamera"; camObject.transform.position = new Vector3(5.5f, 5.2f, -7); camObject.transform.LookAt(avatar.transform.position + Vector3.up);
            var camera = camObject.AddComponent<Camera>(); camera.fieldOfView = 52; camera.backgroundColor = new Color(.77f, .84f, .85f); camera.clearFlags = CameraClearFlags.SolidColor;
            camObject.AddComponent<AudioListener>(); var follow = camObject.AddComponent<FollowCamera>(); follow.target = avatar.transform; follow.controller = controller;
            camObject.AddComponent<RenderFrameProof>().controller = controller;
            var ui = new GameObject("EEG spatial workspace"); ui.transform.SetParent(world.transform);
            var workspace = ui.AddComponent<WorkspaceUI>(); workspace.controller = controller; workspace.desktopCamera = camera;
            avatar.AddComponent<AppliedDirectionFeedback>().controller = controller;
            return controller;
        }
    }
}
