const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const titles = {
  overview: "运行总览",
  review: "发起审查",
  tasks: "任务中心",
  skills: "Skill 注册中心",
  evolution: "演进实验室",
};

const stateLabels = {
  SUBMITTING: "提交中",
  PENDING: "等待中",
  PLANNING: "规划中",
  EXECUTING: "执行中",
  REVIEWING: "汇总中",
  SUCCESS: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

const terminalTaskStates = new Set(["SUCCESS", "FAILED", "CANCELLED"]);
const runStages = [
  { state: "PENDING", code: "QUEUE", title: "任务进入队列", idle: "接收输入并分配执行资源" },
  { state: "PLANNING", code: "PLAN", title: "解析与规划", idle: "解析 Diff，确定文件与审查范围" },
  { state: "EXECUTING", code: "AGENT", title: "Agent 协作审查", idle: "安全、可靠性与 Critic Agent 协同分析" },
  { state: "REVIEWING", code: "GATE", title: "证据与质量门禁", idle: "校验证据、置信度和修复建议" },
  { state: "SUCCESS", code: "REPORT", title: "生成审查报告", idle: "汇总结论并生成可执行报告" },
];
const runStateIndex = Object.fromEntries(runStages.map((stage, index) => [stage.state, index]));

let selectedTask = null;
let selectedTaskData = null;
let accessToken = localStorage.getItem("evoagent_token") || "";
let toastTimer = null;
let reviewPollGeneration = 0;
let taskPollGeneration = 0;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function formatTime(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(date);
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function normalizeState(value) {
  return String(value || "PENDING").toUpperCase();
}

function taskIdentity(task = {}) {
  return task.id || task.task_id || "";
}

function latestTrace(task, state) {
  return [...(task.trace || [])].reverse().find((item) => normalizeState(item.state) === state);
}

function formatTraceMessage(value) {
  const message = String(value || "");
  if (/^Input accepted; preparing review plan$/i.test(message)) return "输入已接收，正在生成审查计划";
  const reviewingFiles = message.match(/^Reviewing (\d+) changed files?$/i);
  if (reviewingFiles) return `正在审查 ${reviewingFiles[1]} 个变更文件`;
  const rankingFindings = message.match(/^Validating and ranking (\d+) findings?$/i);
  if (rankingFindings) return `正在验证并排序 ${rankingFindings[1]} 条候选结论`;
  if (/^Review completed$/i.test(message)) return "审查完成，报告已生成";
  if (/^Review failed:/i.test(message)) return message.replace(/^Review failed:/i, "审查失败：");
  return message;
}

function taskProgressIndex(task = {}) {
  const state = normalizeState(task.state);
  if (state === "SUBMITTING") return 0;
  if (runStateIndex[state] !== undefined) return runStateIndex[state];
  const latestKnown = [...(task.trace || [])]
    .reverse()
    .find((item) => runStateIndex[normalizeState(item.state)] !== undefined);
  return latestKnown ? runStateIndex[normalizeState(latestKnown.state)] : 0;
}

function stateTone(value) {
  const state = normalizeState(value);
  return Object.prototype.hasOwnProperty.call(stateLabels, state) ? state.toLowerCase() : "pending";
}

function riskTone(value) {
  const risk = String(value || "unknown").toLowerCase();
  return ["critical", "high", "medium", "low"].includes(risk) ? risk : "unknown";
}

function runStageDetail(stage, task = {}) {
  const report = task.report || {};
  const execution = report.execution || {};
  if (["FAILED", "CANCELLED"].includes(normalizeState(task.state))
      && stage.state === runStages[taskProgressIndex(task)]?.state
      && task.error) {
    return String(task.error);
  }
  const trace = latestTrace(task, stage.state);
  if (stage.state === "SUCCESS" && normalizeState(task.state) === "SUCCESS" && report.summary) {
    return report.summary;
  }
  if (trace?.message) return formatTraceMessage(trace.message);
  if (stage.state === "PENDING") {
    const id = taskIdentity(task);
    if (normalizeState(task.state) === "SUBMITTING") return "正在创建审查任务…";
    return id ? `${task.queue || "任务"} · ${id.slice(0, 8)}` : stage.idle;
  }
  if (stage.state === "EXECUTING" && task.collaboration?.length) {
    return `${task.collaboration.length} 条 Agent 协作消息已记录`;
  }
  if (stage.state === "REVIEWING" && report.findings) {
    return `${report.findings.length} 条候选结论进入质量门禁`;
  }
  if (stage.state === "SUCCESS" && normalizeState(task.state) === "SUCCESS") {
    return report.summary || `${execution.llm_calls || 0} 次模型调用，报告已就绪`;
  }
  return stage.idle;
}

function renderRunMetric(label, value, tone = "") {
  return `<span class="run-metric ${tone}"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></span>`;
}

function renderRunGraph(task = {}, compact = false) {
  const state = normalizeState(task.state);
  const progress = taskProgressIndex(task);
  const failed = state === "FAILED";
  const cancelled = state === "CANCELLED";
  const succeeded = state === "SUCCESS";
  const report = task.report || {};
  const execution = report.execution || {};
  const id = taskIdentity(task);
  const repository = task.repository || report.repository || "代码审查任务";
  const statusClass = `state-${stateTone(state)}`;
  const nodes = runStages.map((stage, index) => {
    let nodeState = "pending";
    if (succeeded || index < progress) nodeState = "done";
    else if (index === progress) nodeState = failed ? "failed" : cancelled ? "cancelled" : "active";
    const marker = nodeState === "done" ? "✓" : nodeState === "failed" ? "!" : nodeState === "cancelled" ? "×" : String(index + 1).padStart(2, "0");
    const nodeLabel = nodeState === "done" ? "已完成" : nodeState === "active" ? "进行中" : nodeState === "failed" ? "失败" : nodeState === "cancelled" ? "已取消" : "等待";
    return `<div class="run-node is-${nodeState}">
      <span class="run-node-marker" data-code="${stage.code}">${marker}</span>
      <span class="run-node-copy">
        <strong>${escapeHtml(stage.title)}</strong>
        <small>${escapeHtml(runStageDetail(stage, task))}</small>
      </span>
      <em>${nodeLabel}</em>
    </div>`;
  }).join("");
  const metrics = report && Object.keys(report).length
    ? `<div class="run-metrics">
        ${renderRunMetric("风险等级", report.risk || "未知", `risk-${riskTone(report.risk)}`)}
        ${renderRunMetric("发现问题", String((report.findings || []).length))}
        ${renderRunMetric("审查文件", String((report.files_reviewed || []).length))}
        ${renderRunMetric("模型调用", String(execution.llm_calls || 0))}
      </div>`
    : "";
  return `<section class="run-visual${compact ? " is-compact" : ""}">
    <header class="run-heading">
      <span><small>LIVE EXECUTION</small><strong>${escapeHtml(repository)}</strong>${id ? `<code>${escapeHtml(id)}</code>` : ""}</span>
      <span class="status ${statusClass}">${escapeHtml(stateLabels[state] || state)}</span>
    </header>
    <div class="run-track">${nodes}</div>
    ${metrics}
  </section>`;
}

function renderRawData(value) {
  return `<details class="raw-data"><summary>查看原始 JSON <span>用于调试</span></summary><pre>${escapeHtml(formatJson(value))}</pre></details>`;
}

function renderFindings(report = {}) {
  const findings = report.findings || [];
  if (!findings.length) {
    return `<div class="findings-empty"><b>未发现需要阻断的问题</b><span>本次审查已通过现有规则和质量门禁。</span></div>`;
  }
  const severityLabels = { critical: "严重", high: "高危", medium: "中危", low: "低危" };
  return `<div class="finding-list">${findings.map((finding, index) => {
    const severity = ["critical", "high", "medium", "low"].includes(String(finding.severity).toLowerCase())
      ? String(finding.severity).toLowerCase()
      : "unknown";
    const location = `${finding.path || "未知文件"}:${finding.line || "?"}`;
    return `<article class="finding-card severity-${severity}">
      <header><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(finding.title || finding.rule_id || "未命名问题")}</strong><em>${escapeHtml(severityLabels[severity] || severity)}</em></header>
      <code>${escapeHtml(location)} · ${escapeHtml(finding.rule_id || "NO-RULE")}</code>
      <p>${escapeHtml(finding.explanation || finding.evidence || "暂无详细说明")}</p>
      ${finding.fix ? `<div><b>修复建议</b><span>${escapeHtml(finding.fix)}</span></div>` : ""}
    </article>`;
  }).join("")}</div>`;
}

function renderTaskReport(task) {
  const root = $("#task-report");
  if (!root) return;
  const report = task?.report;
  const summary = report
    ? `<section class="report-content">
        <header><span><small>审查结论</small><strong>${escapeHtml(report.summary || "审查已完成")}</strong></span><em class="risk-badge risk-${riskTone(report.risk)}">${escapeHtml(report.risk || "未知风险")}</em></header>
        ${renderFindings(report)}
      </section>`
    : normalizeState(task?.state) === "FAILED"
      ? `<div class="report-error"><b>任务执行失败</b><span>${escapeHtml(task.error || "请查看执行节点和调试数据")}</span></div>`
      : "";
  root.innerHTML = `${renderRunGraph(task)}${summary}${renderRawData(task)}`;
}

function renderReviewRun(task) {
  const root = $("#review-result");
  root.classList.remove("empty");
  root.innerHTML = `${renderRunGraph(task, true)}${renderRawData(task)}`;
}

function waitFor(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function followSubmittedTask(taskId, generation) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await waitFor(attempt === 0 ? 500 : 1250);
    if (generation !== reviewPollGeneration) return;
    const task = await api(`/v1/tasks/${encodeURIComponent(taskId)}`);
    if (generation !== reviewPollGeneration) return;
    renderReviewRun(task);
    if (terminalTaskStates.has(normalizeState(task.state))) {
      loadDashboard();
      return;
    }
  }
  if (generation === reviewPollGeneration) toast("任务仍在后台执行，可前往任务中心继续查看");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("json") ? await response.json() : await response.text();

  if (response.status === 401) {
    $("#login-overlay").classList.remove("hidden");
    $("#logout").classList.add("hidden");
  }
  if (!response.ok) {
    const plainText = typeof data === "string" && !/<[a-z][\s\S]*>/i.test(data) ? data.trim() : "";
    const message = typeof data === "object"
      ? data.error || data.detail
      : plainText || `请求失败 (${response.status})`;
    throw new Error(message || response.statusText || "请求失败");
  }
  return data;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2600);
}

function setButtonBusy(button, busy, busyText) {
  if (!button) return;
  button.setAttribute("aria-busy", String(busy));
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.textContent = busyText;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

function show(view, updateHash = true) {
  if (!titles[view]) {
    view = "overview";
    history.replaceState(null, "", "#overview");
  }
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$(".nav-item").forEach((element) => {
    const active = element.dataset.view === view;
    element.classList.toggle("active", active);
    element.setAttribute("aria-current", active ? "page" : "false");
  });
  $(`#view-${view}`).classList.add("active");
  $("#page-title").textContent = titles[view];
  document.title = `${titles[view]} · EvoAgent`;
  if (updateHash) history.replaceState(null, "", `#${view}`);

  if (view === "tasks") loadTasks();
  if (view === "skills") loadSkills();
  if (view === "evolution") loadFailures();
  window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => show(button.dataset.view)));
$$("[data-jump]").forEach((button) => button.addEventListener("click", () => show(button.dataset.jump)));
window.addEventListener("hashchange", () => show(location.hash.slice(1), false));

function taskRows(tasks) {
  if (!tasks?.length) {
    return '<div class="empty-state"><span><b>还没有审查任务</b>提交一个 Diff 开始首次审查</span></div>';
  }
  return tasks.map((task) => {
    const state = String(task.state || "PENDING").toUpperCase();
    const repository = escapeHtml(task.repository || "未命名仓库");
    const pr = task.pull_request ? `PR #${escapeHtml(task.pull_request)}` : "手动审查";
    return `
      <button class="task-row" data-task="${escapeHtml(task.id)}" type="button">
        <span class="task-main">
          <span class="task-glyph">PR</span>
          <span class="task-copy">
            <span class="task-name">${repository}</span>
            <span class="task-meta"><span>${pr}</span><span>${escapeHtml(formatTime(task.created_at))}</span></span>
          </span>
        </span>
        <span class="status state-${state.toLowerCase()}">${stateLabels[state] || escapeHtml(state)}</span>
      </button>`;
  }).join("");
}

function bindTasks(root) {
  $$("[data-task]", root).forEach((row) => row.addEventListener("click", () => openTask(row.dataset.task)));
}

function statCard(label, value, note, style, icon) {
  return `<article class="stat ${style}">
    <div class="stat-head"><span>${label}</span><i>${icon}</i></div>
    <b>${value}</b><small>${note}</small>
  </article>`;
}

function renderLlmRuntime(llm = {}, runMode = {}) {
  const enabled = Boolean(llm.enabled);
  const failed = Boolean(llm.error);
  const provider = String(llm.provider || "local");
  const model = String(llm.model || "");
  const detail = failed
    ? "暂时无法读取模型配置"
    : enabled
      ? `${provider} / ${model || "默认模型"}，参与上下文审查与风险判断`
      : "未配置模型；agentic 审查暂不可用";
  const state = failed ? "读取失败" : enabled ? "已启用" : "待配置";
  const runtime = failed
    ? "运行时状态未知"
    : enabled
      ? `${provider} / ${model || "模型已配置"}`
      : "agentic / 需要模型配置";

  const chain = $("#execution-chain");
  if (chain) {
    const scanner = '<div class="agent-step"><b>01</b><span><strong>Tool / Scanner</strong><small>规则、AST 与代码搜索提供事实</small></span><em>事实</em></div>';
    const gate = '<i class="flow-line"></i><div class="agent-step"><b>03</b><span><strong>Gate</strong><small>格式、证据、置信度与发布门禁</small></span><em class="done">门禁</em></div>';
    const llmStep = `<i class="flow-line"></i><div class="agent-step is-active" id="llm-agent-step"><b>02</b><span><strong>4-role LLM Agents</strong><small id="llm-agent-detail">${escapeHtml(detail)}</small></span><em id="llm-agent-state">${escapeHtml(state)}</em></div>`;
    chain.innerHTML = scanner + llmStep + gate;
  }

  const step = $("#llm-agent-step");
  if (step) {
    step.classList.remove("is-pending");
    step.classList.toggle("is-active", enabled);
    step.classList.toggle("is-disabled", !enabled && !failed);
    step.classList.toggle("is-error", failed);
    const detailNode = $("#llm-agent-detail");
    const stateNode = $("#llm-agent-state");
    if (detailNode) detailNode.textContent = detail;
    if (stateNode) stateNode.textContent = state;
  }

  const status = $("#llm-runtime-status");
  status.className = `runtime-status ${failed ? "is-error" : enabled ? "is-active" : "is-disabled"}`;
  status.textContent = state;
  const capability = $("#llm-capability");
  capability.classList.toggle("is-active", enabled);
  capability.classList.toggle("is-disabled", !enabled && !failed);
  capability.classList.toggle("is-error", failed);
  $("#llm-capability-detail").textContent = detail;
  $("#llm-runtime-model").textContent = runtime;
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    renderLlmRuntime(data.llm, data.run_mode);
    const modeSelect = $("#review-mode");
    if (modeSelect) {
      modeSelect.value = "agentic";
      modeSelect.disabled = !data.llm?.enabled;
    }
    $("#system-status").textContent = `${data.queue} · ${data.orchestrator}`;
    const stats = data.stats || {};
    const rate = Math.round(Number(stats.success_rate || 0) * 100);
    $("#stats").innerHTML = [
      statCard("总任务", stats.tasks_total ?? 0, "累计审查任务", "", "ALL"),
      statCard("已完成", stats.tasks_success ?? 0, "通过质量门禁", "success", "OK"),
      statCard("失败", stats.tasks_failed ?? 0, "需要进一步处理", "failed", "ERR"),
      statCard("成功率", `${rate}%`, "全部任务成功率", "rate", "RATE"),
      statCard("待处理案例", stats.unresolved_failure_cases ?? 0, "未解决反馈", "feedback", "OPEN"),
      statCard("活跃 Skills", stats.active_skill_versions ?? 0, "当前生效版本", "skills", "SK"),
    ].join("");
    $("#recent-tasks").innerHTML = taskRows((data.tasks || []).slice(0, 5));
    bindTasks($("#recent-tasks"));
  } catch (error) {
    renderLlmRuntime({ error: true }, {});
    $("#system-status").textContent = "服务连接异常";
    $("#stats").innerHTML = '<div class="empty-state"><span><b>暂时无法读取数据</b>请检查服务状态后重试</span></div>';
    $("#recent-tasks").innerHTML = '<div class="empty-state"><span>数据加载失败</span></div>';
    toast(error.message);
  }
}

async function loadTasks() {
  const root = $("#all-tasks");
  root.innerHTML = '<div class="list-loading"></div><div class="list-loading"></div>';
  try {
    const data = await api("/api/tasks");
    root.innerHTML = taskRows(data.tasks || []);
    bindTasks(root);
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>任务加载失败</span></div>';
    toast(error.message);
  }
}

async function followTaskDetail(id, generation) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    if (attempt > 0) await waitFor(1250);
    if (generation !== taskPollGeneration || selectedTask !== id) return;
    try {
      const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
      if (generation !== taskPollGeneration || selectedTask !== id) return;
      selectedTaskData = task;
      renderTaskReport(task);
      $("#create-fix").classList.toggle("hidden", !(task.report && task.pull_request));
      const feedbackReady = task.state === "SUCCESS" && task.report;
      $("#feedback-panel").classList.toggle("hidden", !feedbackReady);
      if (feedbackReady) {
        populateFeedbackFindings(task.report.findings || []);
        await loadTaskFeedback(id);
      }
      if (terminalTaskStates.has(normalizeState(task.state))) return;
    } catch (error) {
      if (generation !== taskPollGeneration) return;
      $("#task-report").innerHTML = `<div class="report-error"><b>无法读取任务</b><span>${escapeHtml(error.message)}</span></div>`;
      selectedTaskData = null;
      return;
    }
  }
}

function openTask(id) {
  show("tasks");
  selectedTask = id;
  selectedTaskData = null;
  const generation = ++taskPollGeneration;
  $("#task-report").innerHTML = '<div class="report-loading"><i></i><span>正在读取执行节点…</span></div>';
  $("#feedback-panel").classList.add("hidden");
  void followTaskDetail(id, generation);
}

const feedbackLabels = {
  false_positive: "误报",
  missed_issue: "漏报",
  bad_fix: "坏修复",
  accepted: "已接受",
};

function populateFeedbackFindings(findings) {
  const select = $("#feedback-finding");
  select.innerHTML = '<option value="">不关联已有结论</option>' + findings.map((finding, index) => {
    const identity = `${finding.rule_id || "未命名规则"} · ${finding.path || "未知文件"}:${finding.line || "?"}`;
    return `<option value="${index}">${escapeHtml(identity)}</option>`;
  }).join("");
  $("#feedback-result").textContent = "";
}

function renderTaskFeedback(cases) {
  const root = $("#task-feedback-history");
  if (!cases.length) {
    root.innerHTML = '<p class="feedback-empty">尚无反馈。提交后，它会在这里保留并进入后续评测。</p>';
    return;
  }
  root.innerHTML = `<p class="list-section-label">本任务反馈</p>${cases.map((item) => {
    const payload = item.payload || {};
    const finding = payload.finding || {};
    const reference = finding.rule_id
      ? `${finding.rule_id}${finding.path ? ` · ${finding.path}:${finding.line || "?"}` : ""}`
      : "未关联审查结论";
    return `<div class="feedback-case">
      <span class="feedback-case-type">${escapeHtml(feedbackLabels[item.category] || item.category)}</span>
      <span class="feedback-case-copy"><b>${escapeHtml(reference)}</b><small>${escapeHtml(payload.note || "未填写说明")}</small></span>
      <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待评测"}</span>
    </div>`;
  }).join("")}`;
}

async function loadTaskFeedback(taskId) {
  const root = $("#task-feedback-history");
  root.innerHTML = '<p class="feedback-empty">正在读取本任务反馈…</p>';
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(taskId)}/feedback`);
    if (selectedTask === taskId) renderTaskFeedback(data.cases || []);
  } catch (error) {
    root.innerHTML = `<p class="feedback-empty">无法读取反馈历史：${escapeHtml(error.message)}</p>`;
  }
}

async function loadSkills() {
  const root = $("#skill-list");
  root.innerHTML = '<div class="skill-card loading"></div><div class="skill-card loading"></div>';
  try {
    const data = await api("/api/skills");
    renderLlmRuntime(data.llm);
    const skills = (data.skills || []).filter((skill) => skill.name !== "llm-review");
    root.innerHTML = skills.length ? skills.map((skill) => `
      <article class="skill-card">
        <span class="skill-label">${skill.sandboxed ? "SANDBOXED SKILL" : "ACTIVE SKILL"}</span>
        <h3>${escapeHtml(skill.name)}</h3>
        <p>${escapeHtml(skill.description || "暂无能力描述")}</p>
        <span class="skill-meta">v${escapeHtml(skill.version)} · ${escapeHtml(skill.source)}</span>
      </article>`).join("") : '<div class="empty-state"><span><b>尚未加载 Skill</b>扫描目录以加载可用能力</span></div>';
  } catch (error) {
    renderLlmRuntime({ error: true });
    root.innerHTML = '<div class="empty-state"><span>Skills 加载失败</span></div>';
    toast(error.message);
  }
}

async function loadFailures() {
  try {
    const [failuresData, status, runsData] = await Promise.all([
      api("/api/failures"),
      api("/v1/evolution/status"),
      api("/v1/evolution/runs?limit=5"),
    ]);
    $("#evolution-status").textContent = formatJson(status);
    const cases = failuresData.cases || [];
    const runs = runsData.runs || [];
    const failureHtml = cases.length
      ? cases.slice(0, 8).map((item) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">FC</span><span class="task-copy">
              <span class="task-name">${escapeHtml(feedbackLabels[item.category] || item.category)}</span>
              <span class="task-meta"><span>${escapeHtml(item.task_id)}</span><span>${escapeHtml((item.payload || {}).note || "无说明")}</span></span>
            </span></span>
            <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待处理"}</span>
          </div>`).join("")
      : '<div class="empty-state"><span><b>暂无失败反馈</b>系统当前没有未处理案例</span></div>';
    const historyHtml = runs.length
      ? `<p class="list-section-label">最近评测</p>${runs.map((run) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">V${escapeHtml(run.candidate_version)}</span><span class="task-copy">
              <span class="task-name">${escapeHtml(run.decision)}</span>
              <span class="task-meta">${Number(run.candidate_score).toFixed(3)} vs ${Number(run.baseline_score).toFixed(3)}</span>
            </span></span>
          </div>`).join("")}`
      : "";
    $("#failure-list").innerHTML = failureHtml + historyHtml;
  } catch (error) {
    $("#evolution-status").textContent = "暂时无法读取评测状态。";
    $("#failure-list").innerHTML = '<div class="empty-state"><span>反馈加载失败</span></div>';
    toast(error.message);
  }
}

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const body = { repository: values.get("repository"), diff: values.get("diff"), mode: values.get("mode") };
  if (values.get("pull_request")) body.pull_request = Number(values.get("pull_request"));
  const asyncQuery = values.get("async") ? "?async=true" : "";
  const generation = ++reviewPollGeneration;
  renderReviewRun({ state: "SUBMITTING", repository: body.repository, pull_request: body.pull_request });
  setButtonBusy(button, true, "正在提交…");
  try {
    const data = await api(`/v1/reviews${asyncQuery}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const task = { repository: body.repository, pull_request: body.pull_request, ...data };
    renderReviewRun(task);
    toast("审查任务已成功提交");
    loadDashboard();
    if (data.task_id && !terminalTaskStates.has(normalizeState(data.state))) {
      void followSubmittedTask(data.task_id, generation).catch((error) => {
        if (generation === reviewPollGeneration) toast(`实时状态更新中断：${error.message}`);
      });
    }
  } catch (error) {
    renderReviewRun({ state: "FAILED", repository: body.repository, error: error.message });
  } finally {
    setButtonBusy(button, false);
  }
});

$("#create-fix").addEventListener("click", async () => {
  if (!selectedTask) return;
  const button = $("#create-fix");
  setButtonBusy(button, true, "正在创建…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(selectedTask)}/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    selectedTaskData = { ...selectedTaskData, fix_result: data };
    renderTaskReport(selectedTaskData);
    toast("修复分支已创建");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#feedback-category").addEventListener("change", (event) => {
  const missed = event.target.value === "missed_issue";
  $("#feedback-missed-fields").classList.toggle("hidden", !missed);
  $("#feedback-hint").textContent = missed
    ? "补充规则和位置可让候选评测学习更精确的检查点。"
    : "提交后可在本任务和演进实验室查看状态。";
});

$("#feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedTask || !selectedTaskData?.report) return;
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const category = String(values.get("category"));
  const selectedIndex = values.get("finding_index");
  const findings = selectedTaskData.report.findings || [];
  const finding = selectedIndex === "" ? {} : { ...(findings[Number(selectedIndex)] || {}) };
  if (category === "missed_issue") {
    const ruleId = String(values.get("rule_id") || "").trim();
    const path = String(values.get("path") || "").trim();
    const line = Number(values.get("line"));
    if (ruleId) finding.rule_id = ruleId;
    if (path) finding.path = path;
    if (Number.isInteger(line) && line > 0) finding.line = line;
  }
  const output = $("#feedback-result");
  output.textContent = "正在保存反馈…";
  setButtonBusy(button, true, "正在提交…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(selectedTask)}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category,
        finding: Object.keys(finding).length ? finding : null,
        note: String(values.get("note") || "").trim(),
      }),
    });
    output.textContent = `${feedbackLabels[data.category] || data.category}已记录；可在演进实验室等待候选评测。`;
    form.reset();
    $("#feedback-missed-fields").classList.add("hidden");
    $("#feedback-hint").textContent = "提交后可在本任务和演进实验室查看状态。";
    await Promise.all([loadTaskFeedback(selectedTask), loadDashboard()]);
    toast("反馈已记录");
  } catch (error) {
    output.textContent = `提交失败：${error.message}`;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#reload-skills").addEventListener("click", async () => {
  const button = $("#reload-skills");
  setButtonBusy(button, true, "正在扫描…");
  try {
    await api("/v1/skills/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await loadSkills();
    toast("Skills 已重新加载");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#evolution-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在评测…");
  try {
    const data = await api("/v1/evolution/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: values.get("skill_name"), prompt: values.get("prompt") }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("新旧版本回放评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#auto-evolve").addEventListener("click", async () => {
  const button = $("#auto-evolve");
  setButtonBusy(button, true, "正在生成…");
  try {
    const data = await api("/v1/evolution/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: "llm-review" }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("反馈候选评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#refresh").addEventListener("click", async () => {
  const view = location.hash.slice(1) || "overview";
  if (view === "overview") await loadDashboard();
  else if (view === "tasks") await loadTasks();
  else if (view === "skills") await loadSkills();
  else if (view === "evolution") await loadFailures();
  else await loadDashboard();
  toast("数据已刷新");
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在登录…");
  try {
    const data = await api("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: values.get("username"),
        password: values.get("password"),
        tenant_id: values.get("tenant_id"),
      }),
    });
    accessToken = data.access_token;
    localStorage.setItem("evoagent_token", accessToken);
    $("#login-overlay").classList.add("hidden");
    $("#logout").classList.remove("hidden");
    $("#login-error").textContent = "";
    await loadDashboard();
  } catch (error) {
    $("#login-error").textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#logout").addEventListener("click", () => {
  accessToken = "";
  localStorage.removeItem("evoagent_token");
  $("#login-overlay").classList.remove("hidden");
  $("#logout").classList.add("hidden");
});

const diffInput = $('textarea[name="diff"]', $("#review-form"));
const diffStats = $("#diff-stats");
function updateDiffStats() {
  const value = diffInput.value;
  const lines = value ? value.split(/\r?\n/).length : 0;
  diffStats.textContent = `${lines} 行，${value.length} 字符`;
}
diffInput.addEventListener("input", updateDiffStats);
updateDiffStats();

if (accessToken) $("#logout").classList.remove("hidden");
show(location.hash.slice(1) || "overview", false);
loadDashboard();
