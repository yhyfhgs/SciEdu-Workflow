/* SciForge conversational workbench — dependency-free, header-authenticated SSE client. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const SOURCE_REQUIRED = new Set(["variant", "adjust_difficulty", "deepen", "evaluate"]);
  const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
  const MODE_LABELS = {
    new: "出新题",
    evolve: "自进化",
    variant: "微调变式",
    deepen: "深入推演",
    adjust_difficulty: "调整难度",
    evaluate: "评估题目",
    solve: "求解验证",
  };
  const PHASE_LABELS = {
    retrieve: "检索质量合格的知识上下文",
    generate: "生成候选题目",
    next: "选择下一候选",
    prefilter: "确定性预筛",
    solve: "独立求解",
    verify: "执行验证矩阵",
    annotate: "难度与能力标注",
    diagnose: "诊断失败证据",
    evolve: "定向进化候选",
    select: "多目标选择",
    prepare_solution: "准备可评估解答",
    qgen_facade: "兼容生成任务",
  };

  const state = {
    authMode: sessionStorage.getItem("sciforge.authMode") || "open",
    token: sessionStorage.getItem("sciforge.token") || "",
    apiKey: sessionStorage.getItem("sciforge.apiKey") || "",
    conversationId: "",
    conversation: null,
    mode: "new",
    templateId: "",
    runId: "",
    lastEventId: 0,
    streamController: null,
    seenEvents: new Set(),
    selectedKnowledge: null,
    connected: false,
  };

  const dom = {
    badge: $("#connectionBadge"),
    conversationList: $("#conversationList"),
    templateList: $("#templateList"),
    templateCount: $("#templateCount"),
    title: $("#conversationTitle"),
    topTitle: $("#topConversationTitle"),
    messages: $("#messageList"),
    empty: $("#emptyState"),
    composer: $("#composerForm"),
    textarea: $("#messageComposer"),
    send: $("#sendButton"),
    params: $("#parameterPanel"),
    sourceWrap: $("#sourceQuestionFieldWrap"),
    source: $("#sourceQuestionField"),
    variantWrap: $("#variantStrategyFieldWrap"),
    difficulty: $("#difficultyField"),
    difficultyOutput: $("#difficultyOutput"),
    timeline: $("#timelineList"),
    runState: $("#runState"),
    cancel: $("#cancelRunButton"),
    verification: $("#verificationStack"),
    verificationCount: $("#verificationCount"),
    metricCalls: $("#metricCalls"),
    metricTokens: $("#metricTokens"),
    metricDuration: $("#metricDuration"),
    knowledgeResults: $("#knowledgeResults"),
    accountDialog: $("#accountDialog"),
    utilityDialog: $("#utilityDialog"),
    toastRegion: $("#toastRegion"),
  };

  function authHeaders(json = false) {
    const headers = new Headers();
    if (json) headers.set("Content-Type", "application/json");
    if (state.authMode === "bearer" && state.token) {
      headers.set("Authorization", `Bearer ${state.token}`);
    } else if (state.authMode === "key" && state.apiKey) {
      headers.set("X-API-Key", state.apiKey);
    }
    return headers;
  }

  async function api(path, options = {}) {
    const headers = authHeaders(options.body !== undefined);
    if (options.headers) {
      new Headers(options.headers).forEach((value, key) => headers.set(key, value));
    }
    const response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || payload.message || detail;
      } catch (_) {
        // Non-JSON errors retain the HTTP status.
      }
      const error = new Error(String(detail));
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    const type = response.headers.get("content-type") || "";
    return type.includes("json") ? response.json() : response.text();
  }

  function persistAuth() {
    sessionStorage.setItem("sciforge.authMode", state.authMode);
    sessionStorage.setItem("sciforge.token", state.token);
    sessionStorage.setItem("sciforge.apiKey", state.apiKey);
  }

  function setConnection(kind, text) {
    state.connected = kind === "connected";
    dom.badge.className = `connection-badge ${kind}`;
    dom.badge.replaceChildren(document.createElement("i"), document.createTextNode(text));
    const current = $("#currentConnection strong");
    if (current) current.textContent = text;
  }

  function toast(message, kind = "") {
    const item = document.createElement("div");
    item.className = `toast ${kind}`.trim();
    item.textContent = message;
    dom.toastRegion.append(item);
    window.setTimeout(() => item.remove(), 4200);
  }

  function showAccount() {
    if (typeof dom.accountDialog.showModal === "function") dom.accountDialog.showModal();
  }

  async function bootstrap() {
    setConnection("", "正在连接");
    try {
      await Promise.all([loadTemplates(), loadConversations()]);
      setConnection("connected", state.authMode === "open" ? "开放模式" : "已安全连接");
      if (dom.accountDialog.open) dom.accountDialog.close();
    } catch (error) {
      setConnection("error", error.status === 401 ? "需要登录" : "连接失败");
      if (error.status === 401) showAccount();
      else toast(error.message, "error");
    }
  }

  async function loadTemplates() {
    const data = await api("/v1/templates");
    const items = data.items || [];
    dom.templateList.replaceChildren();
    const select = $("#templateField");
    select.replaceChildren(new Option("不使用模板", ""));
    for (const template of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "template-card";
      button.dataset.templateId = template.id;
      button.dataset.mode = template.mode || template.id;
      const symbol = document.createElement("span");
      symbol.className = "template-symbol";
      symbol.textContent = template.id === "evaluate" ? "✓" : template.id === "solve" ? "∫" : "∴";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = template.label;
      const description = document.createElement("small");
      description.textContent = template.description;
      copy.append(title, description);
      button.append(symbol, copy);
      button.addEventListener("click", () => applyTemplate(template));
      dom.templateList.append(button);
      select.add(new Option(template.label, template.id));
    }
    dom.templateCount.textContent = String(items.length);
  }

  function applyTemplate(template) {
    state.templateId = template.id;
    $("#templateField").value = template.id;
    setMode(template.mode || template.id);
    $$(".template-card").forEach((item) => {
      item.classList.toggle("selected", item.dataset.templateId === template.id);
    });
    if (!dom.textarea.value && template.description) dom.textarea.value = template.description;
    dom.textarea.focus();
  }

  async function loadConversations() {
    const data = await api("/v1/conversations?limit=80");
    const items = data.items || [];
    dom.conversationList.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "rail-empty";
      empty.textContent = "尚无会话，开始一次新的出题研究。";
      dom.conversationList.append(empty);
      return;
    }
    for (const conversation of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-item";
      button.classList.toggle("active", conversation.id === state.conversationId);
      const icon = document.createElement("span");
      icon.textContent = "Q";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = conversation.title || "未命名研究";
      const meta = document.createElement("small");
      meta.textContent = `${conversation.discipline} · ${formatDate(conversation.updated_at)}`;
      copy.append(title, meta);
      button.append(icon, copy);
      button.addEventListener("click", () => selectConversation(conversation.id));
      dom.conversationList.append(button);
    }
  }

  async function createConversation() {
    const discipline = $("#disciplineField").value;
    const title = `出题研究 · ${new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date())}`;
    const conversation = await api("/v1/conversations", {
      method: "POST",
      body: { title, discipline },
    });
    await loadConversations();
    await selectConversation(conversation.id);
    closeRails();
    return conversation.id;
  }

  async function selectConversation(id) {
    stopStream();
    state.conversationId = id;
    state.runId = "";
    const [conversation, messages] = await Promise.all([
      api(`/v1/conversations/${encodeURIComponent(id)}`),
      api(`/v1/conversations/${encodeURIComponent(id)}/messages?limit=1000`),
    ]);
    state.conversation = conversation;
    dom.title.textContent = conversation.title || "未命名研究";
    dom.topTitle.textContent = conversation.title || "未命名研究";
    $("#renameConversation").disabled = true;
    renderMessages(messages.items || []);
    await loadConversations();
    closeRails();
  }

  function renderMessages(messages) {
    dom.messages.replaceChildren();
    if (!messages.length) {
      dom.messages.append(dom.empty);
      dom.empty.hidden = false;
      return;
    }
    for (const message of messages) appendMessage(message);
    scrollMessages();
  }

  function appendMessage(message, options = {}) {
    if (dom.empty.isConnected) dom.empty.remove();
    const article = document.createElement("article");
    article.className = `message ${message.role || "assistant"}${options.running ? " running" : ""}`;
    if (message.id) article.dataset.messageId = message.id;
    if (message.run_id) article.dataset.runId = message.run_id;
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = message.role === "user" ? "你" : "S";
    const body = document.createElement("div");
    const head = document.createElement("div");
    head.className = "message-head";
    const author = document.createElement("strong");
    author.textContent = message.role === "user" ? "你" : "SciForge";
    const time = document.createElement("time");
    time.textContent = options.running ? "运行中" : formatDate(message.created_at || new Date().toISOString());
    head.append(author, time);
    const content = document.createElement("div");
    content.className = "message-content";
    if (options.running) {
      const dots = document.createElement("span");
      dots.className = "thinking-dots";
      dots.textContent = "正在建立可验证的证据链…";
      content.append(dots);
    } else {
      renderSafeMarkdown(content, message.content || "");
    }
    body.append(head, content);
    article.append(avatar, body);
    dom.messages.append(article);
    scrollMessages();
    return article;
  }

  function renderSafeMarkdown(container, text) {
    const lines = String(text).replace(/\r/g, "").split("\n");
    let list = null;
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line.trim()) {
        list = null;
        continue;
      }
      const heading = /^(#{1,4})\s+(.+)$/.exec(line);
      if (heading) {
        list = null;
        const node = document.createElement(`h${Math.min(4, heading[1].length + 1)}`);
        node.textContent = heading[2];
        container.append(node);
      } else if (/^[-*]\s+/.test(line)) {
        if (!list) {
          list = document.createElement("ul");
          container.append(list);
        }
        const item = document.createElement("li");
        item.textContent = line.replace(/^[-*]\s+/, "");
        list.append(item);
      } else {
        list = null;
        const paragraph = document.createElement("p");
        paragraph.textContent = line;
        container.append(paragraph);
      }
    }
  }

  function setMode(mode) {
    if (!MODE_LABELS[mode]) return;
    state.mode = mode;
    $$(".mode-chip").forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const needsSource = SOURCE_REQUIRED.has(mode);
    dom.sourceWrap.hidden = !(needsSource || mode === "solve");
    dom.variantWrap.hidden = mode !== "variant";
    dom.source.required = needsSource;
    dom.textarea.placeholder = needsSource
      ? "补充你希望如何加工、验证或评估这道源题（可选）……"
      : mode === "solve"
        ? "粘贴完整题面，或在生成约束中填写源题 ID……"
        : "描述知识范围、希望考查的能力与约束……";
    dom.send.querySelector("span").textContent = mode === "evaluate" ? "开始评估" : "开始推演";
  }

  async function submitMessage(event) {
    event.preventDefault();
    if (!state.connected) {
      showAccount();
      return;
    }
    const content = dom.textarea.value.trim();
    const sourceId = dom.source.value.trim();
    const topics = $("#topicsField").value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
    if (SOURCE_REQUIRED.has(state.mode) && !sourceId) {
      dom.params.hidden = false;
      dom.source.focus();
      toast(`${MODE_LABELS[state.mode]}需要源题 ID`, "error");
      return;
    }
    if (state.mode === "solve" && !sourceId && !content) {
      toast("求解模式需要源题 ID 或完整题面", "error");
      dom.textarea.focus();
      return;
    }
    if (["new", "evolve"].includes(state.mode) && !content && !topics.length) {
      toast("请描述题目目标或至少提供一个知识主题", "error");
      return;
    }
    dom.send.disabled = true;
    try {
      if (!state.conversationId) await createConversation();
      const body = {
        mode: state.mode,
        content,
        source_question_id: sourceId,
        discipline: $("#disciplineField").value,
        subject: $("#subjectField").value.trim(),
        topics,
        difficulty: Number(dom.difficulty.value),
        question_type: $("#questionTypeField").value,
        variant_strategy: $("#variantStrategyField").value,
        output_format: $("#outputTypeField").value === "structured_json" ? "json" : "markdown",
        output_type: $("#outputTypeField").value,
        template_id: $("#templateField").value,
        verification_policy: $("#verificationField").value,
        budget: {
          max_steps: 400,
          max_llm_calls: 100,
          max_tokens: 500000,
          max_wall_time_seconds: 3600,
        },
      };
      appendMessage({ role: "user", content: content || `${MODE_LABELS[state.mode]} · ${sourceId}` });
      const running = appendMessage({ role: "assistant", content: "" }, { running: true });
      resetInspector();
      const accepted = await api(`/v1/conversations/${encodeURIComponent(state.conversationId)}/messages`, {
        method: "POST",
        body,
      });
      running.dataset.runId = accepted.run_id;
      state.runId = accepted.run_id;
      state.lastEventId = 0;
      dom.cancel.disabled = false;
      setRunState("running", "运行中");
      dom.textarea.value = "";
      await streamRun(accepted.run_id);
    } catch (error) {
      toast(error.message, "error");
      await refreshCurrentMessages().catch(() => {});
    } finally {
      dom.send.disabled = false;
    }
  }

  function resetInspector() {
    state.seenEvents.clear();
    dom.timeline.replaceChildren();
    dom.verification.replaceChildren();
    dom.verificationCount.textContent = "0";
    dom.metricCalls.textContent = "0";
    dom.metricTokens.textContent = "0";
    dom.metricDuration.textContent = "0.0s";
  }

  async function streamRun(runId) {
    stopStream();
    const controller = new AbortController();
    state.streamController = controller;
    let reconnects = 0;
    while (!controller.signal.aborted && state.runId === runId) {
      const headers = authHeaders(false);
      if (state.lastEventId) headers.set("Last-Event-ID", String(state.lastEventId));
      let response;
      try {
        response = await fetch(`/v1/runs/${encodeURIComponent(runId)}/events`, {
          headers,
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`事件流连接失败 (${response.status})`);
        reconnects = 0;
        await consumeSSE(response, handleRunEvent, controller.signal);
        const run = await refreshRun(runId);
        if (TERMINAL.has(run.status)) break;
      } catch (error) {
        if (controller.signal.aborted) return;
        reconnects += 1;
        if (reconnects > 4) throw error;
        await delay(Math.min(3000, reconnects * 600));
        continue;
      }
    }
    if (!controller.signal.aborted) {
      await refreshRun(runId).catch(() => null);
      await refreshCurrentMessages();
    }
  }

  async function consumeSSE(response, onEvent, signal) {
    if (!response.body) throw new Error("浏览器不支持流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseSSEBlock(block);
        if (event) onEvent(event);
      }
      if (done) break;
    }
  }

  function parseSSEBlock(block) {
    let id = "";
    let type = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return null;
    let payload;
    try {
      payload = JSON.parse(data.join("\n"));
    } catch (_) {
      payload = { payload: { text: data.join("\n") } };
    }
    return { id: Number(id || payload.id || 0), type, ...payload };
  }

  function handleRunEvent(event) {
    const sequence = Number(event.id || 0);
    if (sequence && state.seenEvents.has(sequence)) return;
    if (sequence) {
      state.seenEvents.add(sequence);
      state.lastEventId = Math.max(state.lastEventId, sequence);
    }
    const type = event.type || event.event || "event";
    const payload = event.payload || {};
    if (type === "usage.recorded") updateUsageFromEvent(payload);
    if (type === "qgen.progress" && payload.verification) {
      renderVerification(payload.verification.evidence || [], payload.verification.passed);
    }
    if (type === "evaluation.verified") {
      renderVerification(payload.evidence || [], payload.passed);
    }
    if (type === "run.succeeded") setRunState("completed", "已完成");
    else if (type === "run.failed" || type === "assistant.error") setRunState("failed", "执行失败");
    else if (type === "run.cancelled" || type === "assistant.cancelled") setRunState("cancelled", "已取消");
    else if (type === "run.paused" || type === "budget.exhausted") setRunState("paused", "已暂停");
    else if (type === "run.started") setRunState("running", "运行中");
    addTimeline(sequence, type, payload);
  }

  function addTimeline(sequence, type, payload) {
    const visible = type.startsWith("run.") || type.startsWith("step.") || [
      "qgen.progress", "evaluation.solution_ready", "evaluation.verified",
      "solution.ready", "assistant.final", "assistant.error", "assistant.cancelled",
      "budget.exhausted",
    ].includes(type);
    if (!visible) return;
    const item = document.createElement("li");
    const failed = type.includes("failed") || type.includes("error");
    const completed = type.includes("succeeded") || type.includes("completed") || type === "assistant.final";
    item.className = failed ? "failed" : completed ? "completed" : "running";
    if (sequence) item.dataset.sequence = String(sequence);
    const dot = document.createElement("i");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    const detail = document.createElement("p");
    if (type === "qgen.progress") {
      title.textContent = PHASE_LABELS[payload.phase] || `阶段：${payload.phase || "处理中"}`;
      detail.textContent = `第 ${payload.round || 0} 轮 · ${payload.route || "继续"}`;
    } else if (type === "step.started") {
      title.textContent = PHASE_LABELS[payload.name] || `开始 ${payload.name || "步骤"}`;
      detail.textContent = `尝试 ${payload.attempt || 1}`;
    } else if (type === "evaluation.verified") {
      title.textContent = payload.passed ? "验证矩阵通过" : "验证矩阵发现问题";
      detail.textContent = `${Object.keys(payload.checks || {}).length} 项检查 · ${payload.evidence?.length || 0} 条证据`;
    } else {
      const labels = {
        "run.created": "任务已创建",
        "run.started": "执行器已启动",
        "run.succeeded": "任务成功完成",
        "run.failed": "任务执行失败",
        "run.cancel_requested": "正在请求安全取消",
        "run.cancelled": "任务已在安全边界取消",
        "run.paused": "任务已暂停",
        "solution.ready": "详细解答已生成",
        "evaluation.solution_ready": "评估解答已准备",
        "assistant.final": "最终结果已写入对话",
        "assistant.error": "错误说明已写入对话",
        "assistant.cancelled": "取消结果已写入对话",
        "budget.exhausted": "运行预算已耗尽",
      };
      title.textContent = labels[type] || type.replaceAll(".", " · ");
      detail.textContent = payload.reason || payload.error || payload.confidence || "证据已持久化";
    }
    copy.append(title, detail);
    item.append(dot, copy);
    dom.timeline.append(item);
    dom.timeline.scrollTop = dom.timeline.scrollHeight;
  }

  function renderVerification(evidence, overallPassed) {
    dom.verification.replaceChildren();
    for (const record of evidence) {
      const status = String(record.status || (record.success ? "passed" : "failed"));
      const card = document.createElement("article");
      card.className = `verification-card ${status}`;
      const icon = document.createElement("span");
      icon.className = "verification-icon";
      icon.textContent = status === "passed" ? "✓" : status === "failed" ? "!" : status === "unavailable" ? "—" : "×";
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `${verificationLabel(record.kind)} · ${statusLabel(status)}`;
      const summary = document.createElement("p");
      summary.textContent = record.summary || record.reason || "无摘要";
      copy.append(title, summary);
      if (record.backend) {
        const backend = document.createElement("pre");
        backend.textContent = `backend: ${record.backend}${record.required ? " · required" : " · optional"}`;
        copy.append(backend);
      }
      card.append(icon, copy);
      dom.verification.append(card);
    }
    if (!evidence.length) {
      const empty = document.createElement("article");
      empty.className = "verification-card neutral";
      empty.textContent = "本次运行尚未产生结构化验证证据。";
      dom.verification.append(empty);
    }
    dom.verificationCount.textContent = String(evidence.length);
    if (typeof overallPassed === "boolean") {
      dom.verification.dataset.overall = overallPassed ? "passed" : "failed";
    }
  }

  async function refreshRun(runId) {
    const run = await api(`/v1/runs/${encodeURIComponent(runId)}`);
    dom.metricCalls.textContent = String(run.usage?.llm_calls ?? 0);
    dom.metricTokens.textContent = formatNumber(run.usage?.tokens ?? 0);
    dom.metricDuration.textContent = `${Number(run.usage?.wall_time_seconds || 0).toFixed(1)}s`;
    if (TERMINAL.has(run.status)) {
      setRunState(run.status === "succeeded" ? "completed" : run.status, statusLabel(run.status));
      dom.cancel.disabled = true;
    }
    const verification = run.output?.generated?.verification || run.output?.verification;
    if (verification?.evidence) renderVerification(verification.evidence, verification.passed);
    return run;
  }

  function updateUsageFromEvent(payload) {
    const usage = payload.usage || payload;
    if (usage.llm_calls !== undefined) dom.metricCalls.textContent = String(usage.llm_calls);
    if (usage.tokens !== undefined) dom.metricTokens.textContent = formatNumber(usage.tokens);
    if (usage.wall_time_seconds !== undefined) dom.metricDuration.textContent = `${Number(usage.wall_time_seconds).toFixed(1)}s`;
  }

  function setRunState(status, label) {
    dom.runState.className = `run-state ${status}`;
    dom.runState.textContent = label;
  }

  async function refreshCurrentMessages() {
    if (!state.conversationId) return;
    const data = await api(`/v1/conversations/${encodeURIComponent(state.conversationId)}/messages?limit=1000`);
    renderMessages(data.items || []);
  }

  function stopStream() {
    if (state.streamController) state.streamController.abort();
    state.streamController = null;
  }

  async function cancelRun() {
    if (!state.runId) return;
    dom.cancel.disabled = true;
    try {
      const result = await api(`/v1/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST", body: {} });
      setRunState(result.status === "cancelled" ? "cancelled" : "running", result.status === "cancelled" ? "已取消" : "取消中");
      toast("取消请求已记录，执行器会在安全边界停止。", "success");
    } catch (error) {
      dom.cancel.disabled = false;
      toast(error.message, "error");
    }
  }

  async function searchKnowledge(event) {
    event.preventDefault();
    const query = $("#knowledgeQuery").value.trim();
    const params = new URLSearchParams({
      q: query,
      discipline: $("#disciplineField").value,
      subject: $("#subjectField").value.trim(),
      limit: "30",
    });
    try {
      const data = await api(`/v1/knowledge/search?${params}`);
      renderKnowledge(data.items || []);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderKnowledge(items) {
    dom.knowledgeResults.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "knowledge-empty";
      empty.textContent = "没有命中当前质量门禁下的知识条目。";
      dom.knowledgeResults.append(empty);
      return;
    }
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "knowledge-item";
      const title = document.createElement("strong");
      title.textContent = `${item.number ? `${item.number} · ` : ""}${item.item_type}`;
      const excerpt = document.createElement("p");
      excerpt.textContent = item.content.slice(0, 180);
      const meta = document.createElement("small");
      meta.textContent = `难度 ${item.difficulty} · ${(item.tags || []).slice(-2).join(" / ")}`;
      button.append(title, excerpt, meta);
      button.addEventListener("click", () => selectKnowledge(item));
      dom.knowledgeResults.append(button);
    }
  }

  function selectKnowledge(item) {
    state.selectedKnowledge = item;
    $("#activeContext").hidden = false;
    $("#activeContextTitle").textContent = item.number || item.tags?.at(-1) || item.item_type;
    const topicField = $("#topicsField");
    const existing = topicField.value.split(/[,，]/).map((part) => part.trim()).filter(Boolean);
    const additions = (item.tags || []).filter((tag) => !existing.includes(tag));
    topicField.value = [...existing, ...additions.slice(-3)].join(", ");
    toast("知识条目已加入生成主题与上下文提示。", "success");
  }

  function selectInspector(name) {
    $$("[data-inspector]").forEach((button) => {
      const active = button.dataset.inspector === name;
      button.setAttribute("aria-selected", String(active));
    });
    for (const key of ["run", "verify", "knowledge"]) {
      $(`#${key}Panel`).hidden = key !== name;
    }
  }

  async function authenticate(action) {
    const username = $("#usernameField").value.trim();
    const password = $("#passwordField").value;
    if (!username || !password) {
      toast("请输入用户名和密码", "error");
      return;
    }
    try {
      const result = await api(`/v1/auth/${action}`, { method: "POST", body: { username, password } });
      state.authMode = "bearer";
      state.token = result.token;
      state.apiKey = "";
      persistAuth();
      await bootstrap();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function saveApiKey() {
    const value = $("#apiKeyField").value.trim();
    if (!value) return toast("请输入 API Key", "error");
    state.authMode = "key";
    state.apiKey = value;
    state.token = "";
    persistAuth();
    bootstrap();
  }

  function useOpenMode() {
    state.authMode = "open";
    state.token = "";
    state.apiKey = "";
    persistAuth();
    bootstrap();
  }

  async function showUsage() {
    try {
      const usage = await api("/v1/usage");
      showUtility("USAGE", "本月用量", [
        `已用：${formatNumber(usage.used || 0)}`,
        usage.unlimited ? "额度：无限" : `剩余：${formatNumber(usage.remaining || 0)} / ${formatNumber(usage.quota || 0)}`,
        `周期：${usage.period_start || "—"} → ${usage.period_end || "—"}`,
      ].join("\n"));
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function showUtility(eyebrow, title, text) {
    $("#utilityEyebrow").textContent = eyebrow;
    $("#utilityTitle").textContent = title;
    const body = $("#utilityBody");
    body.replaceChildren();
    const result = document.createElement("pre");
    result.className = "utility-result";
    result.textContent = text;
    body.append(result);
    dom.utilityDialog.showModal();
  }

  function handleLegacy(action) {
    if (action === "generate") {
      setMode("new");
      dom.textarea.focus();
    } else if (action === "solve") {
      setMode("solve");
      dom.textarea.focus();
    } else if (action === "paper") {
      showUtility("PAPER", "智能组卷", "组卷使用当前领域、科目和目标难度。请通过 API /v1/papers 导出 Markdown 或 DOCX。\n\nChatbox 生成结果也可逐题加入试卷。" );
    } else {
      showUtility("RECOMMEND", "自适应推荐", "登录学习者账户后，/v1/recommend 会根据练习表现与薄弱标签推荐质量合格题目。" );
    }
  }

  function toggleRail(element, trigger, open) {
    element.classList.toggle("open", open);
    trigger?.setAttribute("aria-expanded", String(open));
    $("#railScrim").hidden = !open;
  }

  function closeRails() {
    toggleRail($("#leftRail"), $("#navToggle"), false);
    toggleRail($("#rightRail"), $("#inspectorToggle"), false);
  }

  function bindEvents() {
    $("#accountButton").addEventListener("click", showAccount);
    $("#newConversationButton").addEventListener("click", () => createConversation().catch((error) => toast(error.message, "error")));
    $("#refreshConversations").addEventListener("click", () => loadConversations().catch((error) => toast(error.message, "error")));
    dom.composer.addEventListener("submit", submitMessage);
    dom.textarea.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") dom.composer.requestSubmit();
    });
    $$(".mode-chip").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
    $$("[data-starter]").forEach((button) => button.addEventListener("click", () => {
      dom.textarea.value = button.dataset.starter;
      dom.textarea.focus();
    }));
    $("#parameterToggle").addEventListener("click", (event) => {
      dom.params.hidden = !dom.params.hidden;
      event.currentTarget.setAttribute("aria-expanded", String(!dom.params.hidden));
    });
    dom.difficulty.addEventListener("input", () => {
      dom.difficultyOutput.value = `${dom.difficulty.value} / 10`;
      dom.difficultyOutput.textContent = `${dom.difficulty.value} / 10`;
    });
    $("#templateField").addEventListener("change", (event) => {
      const button = $(`.template-card[data-template-id="${CSS.escape(event.target.value)}"]`);
      button?.click();
    });
    $("#knowledgeFocusButton").addEventListener("click", () => {
      selectInspector("knowledge");
      toggleRail($("#rightRail"), $("#inspectorToggle"), true);
      $("#knowledgeQuery").focus();
    });
    $("#knowledgeSearchForm").addEventListener("submit", searchKnowledge);
    $("#clearContext").addEventListener("click", () => {
      state.selectedKnowledge = null;
      $("#activeContext").hidden = true;
    });
    $$("[data-inspector]").forEach((button) => button.addEventListener("click", () => selectInspector(button.dataset.inspector)));
    dom.cancel.addEventListener("click", cancelRun);
    $("#loginButton").addEventListener("click", () => authenticate("login"));
    $("#registerButton").addEventListener("click", () => authenticate("register"));
    $("#saveApiKeyButton").addEventListener("click", saveApiKey);
    $("#useOpenModeButton").addEventListener("click", useOpenMode);
    $("#clearCredentialsButton").addEventListener("click", () => {
      state.token = "";
      state.apiKey = "";
      state.authMode = "open";
      persistAuth();
      setConnection("", "凭据已清除");
    });
    $$("[data-auth-tab]").forEach((button) => button.addEventListener("click", () => {
      $$("[data-auth-tab]").forEach((tab) => tab.setAttribute("aria-selected", String(tab === button)));
      $$("[data-auth-pane]").forEach((pane) => { pane.hidden = pane.dataset.authPane !== button.dataset.authTab; });
    }));
    $("#usageButton").addEventListener("click", showUsage);
    $$("[data-legacy-action]").forEach((button) => button.addEventListener("click", () => handleLegacy(button.dataset.legacyAction)));
    $("#navToggle").addEventListener("click", () => toggleRail($("#leftRail"), $("#navToggle"), !$("#leftRail").classList.contains("open")));
    $("#inspectorToggle").addEventListener("click", () => toggleRail($("#rightRail"), $("#inspectorToggle"), !$("#rightRail").classList.contains("open")));
    $("#inspectorClose").addEventListener("click", closeRails);
    $("#railScrim").addEventListener("click", closeRails);
    window.addEventListener("beforeunload", stopStream);
  }

  function verificationLabel(kind = "verification") {
    return ({
      solve_confidence: "求解置信度",
      independent_review: "独立审稿",
      non_trivial: "非平凡性",
      solution_relevance: "题解相关性",
      numerical_verification: "数值验证",
      lean_verification: "Lean 形式化",
    })[kind] || String(kind).replaceAll("_", " ");
  }

  function statusLabel(status) {
    return ({ passed: "通过", failed: "失败", unavailable: "不可用", error: "错误", succeeded: "已完成", cancelled: "已取消", paused: "已暂停", running: "运行中" })[status] || status;
  }

  function formatDate(value) {
    if (!value) return "刚刚";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
  }

  function scrollMessages() {
    requestAnimationFrame(() => { dom.messages.scrollTop = dom.messages.scrollHeight; });
  }

  function delay(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  bindEvents();
  setMode("new");
  bootstrap();
})();
