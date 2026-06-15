/**
 * 移动端测试页 — 右侧 Hermes Agent 对话（精简版）
 */
(function (global) {
    'use strict';

    var abortCtl = null;
    var busy = false;

    function $(id) {
        return document.getElementById(id);
    }

    function appendChat(role, text) {
        var box = $('msChatHistory');
        if (!box || !text) return;
        var div = document.createElement('div');
        div.className = 'ms-chat-msg ms-chat-msg--' + (role === 'user' ? 'user' : 'assistant');
        div.textContent = text;
        box.appendChild(div);
        box.scrollTop = box.scrollHeight;
    }

    function setAgentStatus(msg) {
        var el = $('msAgentStatus');
        if (el) el.textContent = msg || '';
    }

    function setBusy(on) {
        busy = !!on;
        var stop = $('msBtnAgentStop');
        var plan = $('msBtnAgentPlan');
        var refine = $('msBtnAgentRefine');
        if (stop) stop.disabled = !busy;
        if (plan) plan.disabled = busy;
        if (refine) refine.disabled = busy;
    }

    function getContext() {
        var st = (global.MobileTesting && global.MobileTesting.state) || {};
        return {
            platform_type: 'android',
            udid: st.udid || '',
            case_id: st.caseId || null,
            project_id: st.projectId || null,
            device_connected: !!st.connected,
        };
    }

    async function apiJson(url, opts) {
        var r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
        var data = await r.json().catch(function () { return {}; });
        if (!r.ok && data.error) throw new Error(data.error);
        return data;
    }

    async function persistPlanSteps(plan) {
        var st = (global.MobileTesting && global.MobileTesting.state) || {};
        if (!st.caseId || !plan || !plan.steps || !plan.steps.length) return 0;
        var n = 0;
        for (var i = 0; i < plan.steps.length; i++) {
            var step = plan.steps[i];
            await apiJson('/api/steps', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    case_id: st.caseId,
                    action: step.action,
                    selector_type: step.strategy || step.selector_type || 'accessibility_id',
                    selector_value: step.selector_value || '',
                    input_value: step.input_value || '',
                    description: step.description || '',
                    automation_layer: 'android',
                    mobile_spec: typeof step.mobile_spec === 'string'
                        ? step.mobile_spec
                        : JSON.stringify(step.mobile_spec || {}),
                }),
            });
            n++;
        }
        if (global.MobileTesting && global.MobileTesting.loadSteps) {
            await global.MobileTesting.loadSteps();
        }
        return n;
    }

    async function runPlan() {
        var input = $('msAgentInput');
        var goal = (input && input.value || '').trim();
        if (!goal) {
            setAgentStatus('请输入任务目标');
            return;
        }
        appendChat('user', goal);
        setBusy(true);
        setAgentStatus('正在生成用例…');
        abortCtl = new AbortController();
        try {
            var projSel = $('msProjectSelect');
            var data = await apiJson('/api/ai/task/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: abortCtl.signal,
                body: JSON.stringify({
                    goal: goal,
                    project_name: projSel && projSel.selectedOptions[0]
                        ? projSel.selectedOptions[0].text : '',
                    platform_type: 'android',
                    mobile_context: getContext(),
                }),
            });
            if (!data.success || !data.plan) {
                appendChat('assistant', data.error || '生成失败');
                setAgentStatus('生成失败');
                return;
            }
            var preview = JSON.stringify(data.plan, null, 2);
            appendChat('assistant', '已生成 ' + (data.plan.steps || []).length + ' 步预览。\n' + preview.slice(0, 1200));
            var n = await persistPlanSteps(data.plan);
            setAgentStatus(n ? ('已写入 ' + n + ' 个 Android 步骤') : '已生成预览（请选择用例后再次生成以写入）');
        } catch (e) {
            if (e && e.name === 'AbortError') {
                setAgentStatus('已停止');
            } else {
                appendChat('assistant', (e && e.message) || String(e));
                setAgentStatus('错误');
            }
        } finally {
            abortCtl = null;
            setBusy(false);
        }
    }

    async function runRefine() {
        var input = $('msAgentInput');
        var goal = (input && input.value || '').trim();
        if (!goal) {
            setAgentStatus('请输入优化说明');
            return;
        }
        appendChat('user', '[优化] ' + goal);
        setBusy(true);
        setAgentStatus('正在优化…');
        abortCtl = new AbortController();
        try {
            var data = await apiJson('/api/ai/task/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: abortCtl.signal,
                body: JSON.stringify({
                    message: goal,
                    platform_type: 'android',
                    mobile_context: getContext(),
                }),
            });
            var reply = data.reply || data.message || data.content || JSON.stringify(data).slice(0, 2000);
            appendChat('assistant', String(reply).slice(0, 4000));
            if (data.plan) {
                var n = await persistPlanSteps(data.plan);
                if (n) setAgentStatus('已追加 ' + n + ' 步');
            } else {
                setAgentStatus('优化完成');
            }
        } catch (e) {
            if (e && e.name === 'AbortError') {
                setAgentStatus('已停止');
            } else {
                appendChat('assistant', (e && e.message) || String(e));
                setAgentStatus('错误');
            }
        } finally {
            abortCtl = null;
            setBusy(false);
        }
    }

    function stopAgent() {
        if (abortCtl) abortCtl.abort();
        setBusy(false);
        setAgentStatus('已停止');
    }

    function wireUi() {
        var plan = $('msBtnAgentPlan');
        var refine = $('msBtnAgentRefine');
        var stop = $('msBtnAgentStop');
        if (plan) plan.addEventListener('click', function () { runPlan().catch(function (e) { setAgentStatus(e.message); }); });
        if (refine) refine.addEventListener('click', function () { runRefine().catch(function (e) { setAgentStatus(e.message); }); });
        if (stop) stop.addEventListener('click', stopAgent);
        var capsEl = document.getElementById('__mobileStudioCapsJson');
        if (capsEl) {
            try { global.__mobileStudioCaps = JSON.parse(capsEl.textContent); } catch (e) {}
        }
        appendChat('assistant', '移动端 Agent 已就绪。左侧连接设备并选择用例后，可描述测试目标生成 Android 步骤。');
    }

    global.MobileAgentPanel = { wireUi: wireUi, appendChat: appendChat, setAgentStatus: setAgentStatus };
    document.addEventListener('DOMContentLoaded', wireUi);
})(window);
