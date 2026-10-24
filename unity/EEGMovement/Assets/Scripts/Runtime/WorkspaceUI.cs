using System;
using EEGMovement.Core;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
using UnityEngine.XR.Interaction.Toolkit.UI;

namespace EEGMovement.Unity
{
    // One hierarchy for pointer, keyboard and tracked XR rays. No UI action creates a motion command.
    public sealed class WorkspaceUI : MonoBehaviour
    {
        public EEGAvatarController controller;
        public Camera desktopCamera;
        Canvas canvas;
        Font font;
        Sprite rounded;
        RectTransform root, panel, summary, footer;
        Text status, summaryTitle, summaryBody, actual, mode, details, testLabel, resetLabel, identity;
        GameObject linkPage, settingsPage, collectionPage;
        GuidedAcquisition acquisition;
        Text collectionPrompt, collectionFixation, collectionStatus;
        Button collectionStart, collectionPause, collectionStop;
        Button testButton, resetButton;
        Button[] tabs = new Button[4];
        string page;
        float transition = 1;
        CanvasGroup pageGroup;
        GameObject spatialRoot; GameObject[] desktopViews; bool spatialMode;
        public void HidePanel() { var rig = GetComponent<SpatialWorkspaceRig>(); if (rig) rig.SetPanelVisible(false); }
        public void SetSpatial(bool active)
        {
            if (!spatialRoot)
            {
                desktopViews = new GameObject[root.childCount]; for (int i = 0; i < root.childCount; i++) desktopViews[i] = root.GetChild(i).gameObject;
                spatialRoot = new GameObject("Headset workspace", typeof(RectTransform), typeof(Image)); spatialRoot.transform.SetParent(root, false);
                var surface = spatialRoot.GetComponent<Image>(); surface.color = Color.clear; surface.raycastTarget = true;
                var r = spatialRoot.GetComponent<RectTransform>(); r.anchorMin = Vector2.zero; r.anchorMax = Vector2.one; r.offsetMin = r.offsetMax = Vector2.zero;
                spatialRoot.AddComponent<HeadsetWorkspace>().Initialize(this, controller, font, rounded);
            }
            spatialMode = active; spatialRoot.SetActive(active); foreach (var view in desktopViews) view.SetActive(!active);
            if (!active) Show(controller.Workspace.Page);
        }
        Color ink = Hex("17212B"), muted = Hex("52616E"), accent = Hex("006C85"), danger = Hex("B42336");
        static Color Hex(string value) { Color c; ColorUtility.TryParseHtmlString("#" + value, out c); return c; }
        void Start()
        {
            font = Resources.Load<Font>("Fonts/NotoSansCJKsc-Regular");
            if (!font) throw new InvalidOperationException("Redistributable Noto CJK font is required.");
            rounded = RoundSprite();
            var go = new GameObject("EEG Workspace · shared screen and spatial canvas", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster), typeof(TrackedDeviceGraphicRaycaster));
            go.transform.SetParent(transform, false); canvas = go.GetComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceOverlay; canvas.sortingOrder = 20;
            var scaler = go.GetComponent<CanvasScaler>(); scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize; scaler.referenceResolution = new Vector2(1440, 900); scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.Expand; // Preserve at least 1440 x 900 logical units so navigation and stop never overlap.
            root = go.GetComponent<RectTransform>();
            if (!FindFirstObjectByType<EventSystem>())
            {
                var events = new GameObject("Workspace Input · mouse keyboard and XR", typeof(EventSystem), typeof(XRUIInputModule));
                events.transform.SetParent(transform, false); var module = events.GetComponent<XRUIInputModule>(); module.enableBuiltinActionsAsFallback = true;
            }
            var header = Card(root, "Navigation", new Vector2(24, -24), new Vector2(-48, 88), new Vector2(0, 1), new Vector2(1, 1));
            Label(header, "EEGMovement", 27, ink, 24, -17, 250, 40, FontStyle.Bold);
            Label(header, "运动反馈工作区", 15, muted, 25, -53, 250, 24);
            string[] labels = { "体验", "链路状态", "设置", "脑电采集" }; string[] pages = { "experience", "link", "settings", "collection" };
            for (int i = 0; i < tabs.Length; i++) { string target = pages[i]; tabs[i] = Action(header, labels[i], 330 + i * 140, -19, 132, 50, () => Show(target), false); }
            status = Label(header, "等待配对", 21, ink, -270, -25, 240, 34, FontStyle.Bold); status.rectTransform.anchorMin = status.rectTransform.anchorMax = new Vector2(1, 1);
            summary = Card(root, "Experience summary", new Vector2(32, -136), new Vector2(380, 250));
            Label(summary, "当前体验", 16, muted, 24, -20, 330, 28);
            summaryTitle = Label(summary, "人物保持静止", 29, ink, 24, -57, 332, 48, FontStyle.Bold);
            summaryBody = Label(summary, "等待中枢连接。\n连接完成后查看准备条件。", 19, muted, 24, -115, 332, 105);
            panel = Card(root, "Details panel", new Vector2(-32, -136), new Vector2(490, 540), new Vector2(1, 1), new Vector2(1, 1)); panel.pivot = new Vector2(1, 1);
            pageGroup = panel.gameObject.AddComponent<CanvasGroup>();
            linkPage = Page(panel, "Link details");
            Label(linkPage.transform, "链路状态", 28, ink, 26, -22, 430, 48, FontStyle.Bold);
            details = Label(linkPage.transform, "等待接收器", 19, muted, 26, -90, 438, 245);
            Label(linkPage.transform, "合成链路测试 · TEST", 20, ink, 26, -344, 438, 36, FontStyle.Bold);
            Label(linkPage.transform, "仅接收中枢有期限的测试指令。\n离开此页即撤销允许，人物停止。", 17, muted, 26, -390, 438, 58);
            testButton = Action(linkPage.transform, "允许 TEST 合成指令", 26, -466, 438, 52, () => { controller.AllowTests(!controller.Workspace.AllowTests); Refresh(); }, true);
            testLabel = testButton.GetComponentInChildren<Text>();
            settingsPage = Page(panel, "Accessibility settings");
            Label(settingsPage.transform, "设置", 28, ink, 26, -22, 438, 48, FontStyle.Bold);
            Label(settingsPage.transform, "阅读与交互", 19, muted, 26, -82, 438, 32);
            var motion = Action(settingsPage.transform, "减少动态效果", 26, -132, 438, 58, () => { controller.ReduceMotion = !controller.ReduceMotion; controller.SavePreferences(); }, false);
            var transparency = Action(settingsPage.transform, "减少透明效果", 26, -210, 438, 58, () => { controller.ReduceTransparency = !controller.ReduceTransparency; controller.SavePreferences(); }, false);
            var large = Action(settingsPage.transform, "增大文字", 26, -288, 438, 58, () => { controller.LargeText = !controller.LargeText; controller.SavePreferences(); }, false);
            preferences = new[] { motion.GetComponentInChildren<Text>(), transparency.GetComponentInChildren<Text>(), large.GetComponentInChildren<Text>() };
            Label(settingsPage.transform, "空间模式下：使用控制器射线指向并按扳机。\n控制器菜单键 / 桌面 Esc 立即急停。\n重新居中只调整视角，不改变人物坐标。", 17, muted, 26, -370, 438, 128);
            acquisition = gameObject.AddComponent<GuidedAcquisition>(); acquisition.Controller = controller; controller.Acquisition = acquisition;
            var manual=gameObject.AddComponent<SelfPacedAcquisition>();manual.Controller=controller;controller.SelfPaced=manual;
            collectionPage = Page(panel, "Guided EEG collection");
            Label(collectionPage.transform, "脑电采集 · 引导任务", 27, ink, 26, -22, 438, 44, FontStyle.Bold);
            collectionPrompt = Label(collectionPage.transform, "", 19, muted, 26, -82, 438, 90);
            collectionFixation = Label(collectionPage.transform, "+", 82, accent, 26, -174, 438, 96); collectionFixation.alignment = TextAnchor.MiddleCenter;
            collectionStatus = Label(collectionPage.transform, "", 16, muted, 26, -276, 438, 72);
            collectionStart = Action(collectionPage.transform, "白底持续引导 · 约13分钟", 26, -354, 438, 50, () => acquisition.Begin(2), true);
            collectionPause = Action(collectionPage.transform, "自主按住采集 · 任意时长", 26, -416, 438, 46, () => controller.SelfPaced.Begin(), false);
            collectionStop = Action(collectionPage.transform, "停止采集并保存", 26, -474, 438, 46, () => {acquisition.Abort();controller.SelfPaced.Finish();}, false);
            footer = Card(root, "Persistent execution controls", new Vector2(24, 24), new Vector2(-48, 144), new Vector2(0, 0), new Vector2(1, 0)); footer.pivot = new Vector2(0, 0);
            mode = Label(footer, "LIVE · 等待有效模型", 17, muted, 24, -18, 360, 28);
            actual = Label(footer, "— m/s     — °/s", 28, ink, 24, -58, 360, 48, FontStyle.Bold);
            Action(footer, "准备检查", 388, -25, 150, 52, () => Show("link"), true);
            Action(footer, "重新居中", 554, -25, 150, 52, Recenter, false);
            Action(footer, "重新连接", 388, -89, 150, 40, () => { controller.Reconnect(); Refresh(); }, false);
            resetButton = Action(footer, "手动复位", 554, -89, 150, 40, () => { controller.ResetEmergency(); Refresh(); }, false); resetLabel = resetButton.GetComponentInChildren<Text>();
            var stop = Action(footer, "立即急停   Esc", -322, -26, 292, 88, () => { controller.EmergencyStop(); Refresh(); }, true);
            stop.gameObject.AddComponent<ImmediateStopPress>().controller = controller;
            stop.GetComponent<Image>().color = danger; var stopRect = stop.GetComponent<RectTransform>(); stopRect.anchorMin = stopRect.anchorMax = new Vector2(1, 1);
            var stopColors = stop.colors; stopColors.normalColor = Color.white; stopColors.highlightedColor = new Color(1, .86f, .86f); stopColors.pressedColor = new Color(.78f, .78f, .78f); stop.colors = stopColors;
            stop.GetComponentInChildren<Text>().fontSize = 26;
            identity = Label(root, "", 13, muted, 32, 183, 1200, 24); identity.rectTransform.anchorMin = identity.rectTransform.anchorMax = Vector2.zero; identity.rectTransform.pivot = new Vector2(0, 0);
            gameObject.AddComponent<SpatialWorkspaceRig>().Initialize(canvas, desktopCamera, controller, this);
            Show(controller.Workspace.Page); Refresh();
            if (EventSystem.current) EventSystem.current.SetSelectedGameObject(tabs[0].gameObject);
        }
        Text[] preferences;
        GameObject Page(Transform parent, string name) { var go = new GameObject(name, typeof(RectTransform)); go.transform.SetParent(parent, false); var r = go.GetComponent<RectTransform>(); r.anchorMin = Vector2.zero; r.anchorMax = Vector2.one; r.offsetMin = r.offsetMax = Vector2.zero; return go; }
        RectTransform Card(Transform parent, string name, Vector2 pos, Vector2 size, Vector2? min = null, Vector2? max = null)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image)); go.transform.SetParent(parent, false);
            var r = go.GetComponent<RectTransform>(); r.anchorMin = min ?? new Vector2(0, 1); r.anchorMax = max ?? r.anchorMin; r.pivot = new Vector2(0, 1); r.anchoredPosition = pos; r.sizeDelta = size;
            var image = go.GetComponent<Image>(); image.sprite = rounded; image.type = Image.Type.Sliced; image.color = new Color(.97f, .98f, .99f, .97f); image.raycastTarget = false;
            return r;
        }
        Text Label(Transform parent, string value, int size, Color color, float x, float y, float width, float height, FontStyle style = FontStyle.Normal)
        {
            var go = new GameObject(value.Length > 32 ? "Text" : value, typeof(RectTransform), typeof(Text)); go.transform.SetParent(parent, false);
            var r = go.GetComponent<RectTransform>(); r.anchorMin = r.anchorMax = new Vector2(0, 1); r.pivot = new Vector2(0, 1); r.anchoredPosition = new Vector2(x, y); r.sizeDelta = new Vector2(width, height);
            var t = go.GetComponent<Text>(); t.font = font; t.text = value; t.fontSize = size; t.fontStyle = style; t.color = color; t.raycastTarget = false; t.horizontalOverflow = HorizontalWrapMode.Wrap; t.verticalOverflow = VerticalWrapMode.Truncate; t.supportRichText = false;
            return t;
        }
        Button Action(Transform parent, string value, float x, float y, float width, float height, UnityEngine.Events.UnityAction action, bool primary)
        {
            var r = Card(parent, value, new Vector2(x, y), new Vector2(width, height));
            var image = r.GetComponent<Image>(); image.color = primary ? accent : new Color(.88f, .92f, .94f); image.raycastTarget = true;
            var button = r.gameObject.AddComponent<Button>(); button.targetGraphic = image;
            var c = button.colors; c.normalColor = Color.white; c.highlightedColor = new Color(.80f, .93f, 1); c.selectedColor = new Color(.66f, .85f, .96f); c.pressedColor = new Color(.65f, .76f, .84f); c.disabledColor = new Color(.68f, .70f, .72f, .75f); c.fadeDuration = .1f; button.colors = c;
            button.onClick.AddListener(action);
            var text = Label(r, value, 20, primary ? Color.white : ink, 10, 0, width - 20, height); text.alignment = TextAnchor.MiddleCenter;
            return button;
        }
        public void Show(string target)
        {
            if (acquisition && target != "collection") acquisition.Abort("page_exit");
            controller.Navigate(target); page = controller.Workspace.Page;
            panel.gameObject.SetActive(page != "experience"); linkPage.SetActive(page == "link"); settingsPage.SetActive(page == "settings"); collectionPage.SetActive(page == "collection");
            transition = controller.ReduceMotion ? 1 : 0; pageGroup.alpha = transition;
            for (int i = 0; i < tabs.Length; i++) tabs[i].GetComponent<Image>().color = i == (page == "experience" ? 0 : page == "link" ? 1 : page == "settings" ? 2 : 3) ? new Color(.72f, .86f, .90f) : new Color(.91f, .94f, .96f);
            Refresh();
        }
        void Update()
        {
            if (!canvas || !controller) return;
            if (spatialMode) return;
            if (page != controller.Workspace.Page) Show(controller.Workspace.Page);
            transition = controller.ReduceMotion ? 1 : Mathf.MoveTowards(transition, 1, Time.unscaledDeltaTime / .2f); pageGroup.alpha = transition;
            Refresh();
        }
        void Refresh()
        {
            if (!status) return;
            var s = controller.State; bool emergency = controller.Workspace.EmergencyLatched;
            bool fresh = s != null && s.paired && s.command_age_ms >= 0 && s.command_age_ms <= 180;
            bool moving = fresh && !emergency && (Math.Abs(s.actual_speed) > .005 || Math.Abs(s.actual_yaw_rate) > .1);
            status.text = emergency ? "已急停 · 锁止中" : moving ? "人物正在执行" : fresh ? "已连接 · 人物静止" : "等待新鲜指令"; status.color = emergency ? danger : ink;
            summaryTitle.text = emergency ? "急停已经生效" : moving ? "人物正在执行" : "人物保持静止";
            summaryBody.text = emergency ? "运动已锁止。手动复位后仍保持静止，需重新检查准备条件。" : controller.Issue ?? (controller.Workspace.AllowTests ? "TEST 已显式允许。\n请从中枢发起限期合成指令。" : fresh ? "LIVE 安全保持静止。\n尚未接入已校准的个人意图模型。" : "等待中枢配对与新鲜指令。\n进入“链路状态”检查连接。" );
            mode.text = controller.Workspace.AllowTests ? "TEST 已允许 · 合成指令" : "LIVE · TEST 未允许";
            actual.text = fresh ? s.actual_speed.ToString("F2") + " m/s    " + s.actual_yaw_rate.ToString("F1") + " °/s" : "执行回执过期 / 未知";
            testButton.interactable = !emergency; testLabel.text = controller.Workspace.AllowTests ? "撤销 TEST 允许并停止" : "允许 TEST 合成指令";
            resetButton.interactable = emergency; resetLabel.text = emergency ? "手动复位" : "无急停锁止";
            details.text = s == null ? "接收器未连接\n\n" + (controller.Issue ?? "请打开本机中枢并重新连接。") :
                "接收器  Unity · " + (s.paired ? "已配对" : "等待配对") + "\n指令状态  " + Reason(s.reason) + "\n回执新鲜度  " + (fresh ? Math.Max(0, s.command_age_ms).ToString("F0") + " ms" : "已过期 / 无指令") +
                "\n实际位置  " + s.x.ToString("F2") + " / " + s.z.ToString("F2") + " m\n已应用序号  " + s.applied_seq +
                "\n帧耗时 / P95  " + controller.Health.frame_ms.ToString("F1") + " / " + controller.Health.frame_p95_ms.ToString("F1") + " ms\n拒绝包 / 看门狗  " + s.rejected + " / " + s.watchdog_stops;
            if (acquisition)
            {
                collectionPrompt.text = acquisition.Prompt; collectionFixation.text = acquisition.Fixation;
                collectionStatus.text = acquisition.Status + "\n保存事件 " + acquisition.Saved + " · " + (acquisition.Progress * 100).ToString("F0") + "% · 软件显示事件，非硬件触发";
                bool manualBusy=controller.SelfPaced&&(controller.SelfPaced.Active||controller.SelfPaced.Busy);
                collectionStart.interactable = !acquisition.Busy && !acquisition.Active&&!manualBusy;
                collectionPause.interactable = !acquisition.Busy&&!acquisition.Active&&!manualBusy; collectionStop.interactable = acquisition.Busy || acquisition.Active||manualBusy;
                collectionPause.GetComponentInChildren<Text>().text = "自主按住采集 · 任意时长";
                resetButton.interactable = emergency && !acquisition.Busy && !acquisition.Active;
            }
            preferences[0].text = "减少动态效果   " + (controller.ReduceMotion ? "开" : "关"); preferences[1].text = "减少透明效果   " + (controller.ReduceTransparency ? "开" : "关"); preferences[2].text = "增大文字   " + (controller.LargeText ? "开" : "关");
            foreach (var rect in new[] { summary, footer, panel }) { var image = rect.GetComponent<Image>(); var color = image.color; color.a = controller.ReduceTransparency ? 1 : .97f; image.color = color; }
            summaryBody.fontSize = controller.LargeText ? 22 : 19; details.fontSize = controller.LargeText ? 21 : 19;
            identity.text = "构建 " + controller.Health.build_id + "   ·   " + (controller.Health.xr_active ? "XR 空间模式 · 头显体验待实测" : "桌面模式 · 尚不能代表头显验收");
        }
        static string Reason(string value)
        {
            switch (value) { case "waiting_pairing": return "等待配对"; case "paired_waiting_command": return "等待新鲜指令"; case "watchdog_timeout": return "指令到期，已停止"; case "local_emergency_stop": return "本机急停锁止"; case "manual_reset": return "已复位，保持静止"; case "active": return "已应用运动"; case "idle": return "静止"; case "confirming": return "等待独立决策确认"; case "uncertain": return "置信不足，已停止"; case "pairing_unavailable": return "配对不可用"; default: return value ?? "等待指令"; }
        }
        public void Recenter()
        {
            var rig = GetComponent<SpatialWorkspaceRig>(); if (rig && rig.Active) rig.RecenterPanel();
            else { var follow = desktopCamera.GetComponent<FollowCamera>(); if (follow) follow.Recenter(); }
        }
        static Sprite RoundSprite()
        {
            var texture = new Texture2D(64, 64, TextureFormat.RGBA32, false); texture.name = "Workspace rounded material";
            for (int y = 0; y < 64; y++) for (int x = 0; x < 64; x++)
            { float dx = Mathf.Max(16 - x, x - 47, 0), dy = Mathf.Max(16 - y, y - 47, 0); float alpha = Mathf.Clamp01(16.5f - Mathf.Sqrt(dx * dx + dy * dy)); texture.SetPixel(x, y, new Color(1, 1, 1, alpha)); }
            texture.Apply(); return Sprite.Create(texture, new Rect(0, 0, 64, 64), new Vector2(.5f, .5f), 100, 0, SpriteMeshType.FullRect, new Vector4(18, 18, 18, 18));
        }
    }
}
