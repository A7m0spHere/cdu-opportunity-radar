/* No cookies, persistence, third-party requests, or stale-data fallback. */
"use strict";
const $ = (id) => document.getElementById(id);
const viewButtons = [...document.querySelectorAll("[data-view]")];
const selectionButtons = [...document.querySelectorAll("[data-selection]")];
const audienceButtons = [...document.querySelectorAll("[data-audience]")];
const statuses = {possible: "可能仍可参与", closed: "报名已截止", unknown: "报名状态未确认"};
const labels = {all: "最新通知", current: "当前机会", history: "历史信息"};
let data = null, view = "all", selection = "all", audience = "undergraduate", pollTimer, expiryTimer, requestController, generation = 0;
const expandedClassifications = new Set(), expandedFeatured = new Set();
const filterControls = {source: "publisher", type: "information-type", domain: "domain", topic: "topic", status: "registration-status", published: "date-presence", from: "date-from", to: "date-to"};
const subscriptionHelp = "订阅最多返回最新 100 条；精选分析尚未就绪时会暂不可用。链接仅在当前服务运行且可访问时有效。";

function queryPath() {
  const params = new URLSearchParams();
  if (selection !== "all") params.set("selection", selection);
  if (audience !== "all") params.set("audience", audience);
  for (const [key,id] of Object.entries(filterControls)) {
    const value = $(id).value;
    if (value && value !== "all") params.set(key, value);
  }
  return "/api/notices/v4" + (params.size ? "?" + params.toString() : "");
}

function populateFilters(payload) {
  for (const [key,id,label] of [["sources","publisher","全部来源"],["types","information-type","全部类型"],["domains","domain","全部领域"],["topics","topic","全部学科"]]) {
    const selected = $(id).value;
    const values = [{id: "all", label}, ...payload.filter_options[key]];
    if (key === "topics" || key === "domains") values.push({id: "unclassified", label: "未分类"});
    $(id).replaceChildren(...values.map((v) => Object.assign(document.createElement("option"), {value: v.id, textContent: v.label})));
    if (values.some((v) => v.id === selected)) $(id).value = selected;
  }
}

function formatTime(value) {
  const stamp = new Date(value);
  if (!Number.isFinite(stamp.getTime())) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(stamp);
}

function sourceUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      (url.port && !["80", "443"].includes(url.port)) ||
      !(url.hostname === "cdu.edu.cn" || url.hostname.endsWith(".cdu.edu.cn"))) throw new Error("Invalid official URL");
  return url.href;
}

function validate(payload) {
  if (payload.schema_version !== 4 || payload.taxonomy_version !== 2 || !payload.filter_options || !payload.classification_review ||
      !["sources","types","topics","domains"].every((key) => Array.isArray(payload.filter_options[key]) && payload.filter_options[key].every((v) => typeof v.id === "string" && typeof v.label === "string")) ||
      !Array.isArray(payload.items) || !Array.isArray(payload.categories) ||
      !Number.isFinite(payload.valid_for_ms) || payload.valid_for_ms <= 0 || payload.valid_for_ms > 3600000 ||
      !["live", "offline_replay"].includes(payload.data_mode) || !payload.refreshed_at) throw new Error("Invalid response");
  if (!payload.featured || !["pending","degraded","ready"].includes(payload.featured.state) || !payload.featured.coverage || payload.subscription?.available !== true || typeof payload.subscription.url !== "string" || !/^\/rss\/v2\/(all|featured)\.xml(\?|$)/.test(payload.subscription.url)) throw new Error("Invalid featured response");
  const subscription = new URL(payload.subscription.url, location.origin);
  if (subscription.origin !== location.origin || subscription.hash || !["/rss/v2/all.xml", "/rss/v2/featured.xml"].includes(subscription.pathname)) throw new Error("Invalid subscription");
  if (!payload.featured.ranges || !["current","history"].every((key) => payload.featured.ranges[key]?.coverage && ["pending","degraded","ready"].includes(payload.featured.ranges[key].state))) throw new Error("Invalid range state");
  const guids = new Set(), urls = new Set();
  payload.items = payload.items.filter((item) => {
    for (const key of ["guid", "title", "source_name", "source_url", "summary", "source_checked_at", "first_aggregated_at"]) {
      if (typeof item[key] !== "string" || !item[key]) throw new Error("Invalid item");
    }
    if (!Object.hasOwn(statuses, item.registration_status) || !["current", "history", "unknown"].includes(item.listing) ||
        !payload.categories.includes(item.category) || (item.source_published_date !== null && !/^20\d{2}-\d{2}-\d{2}$/.test(item.source_published_date))) throw new Error("Invalid state");
    sourceUrl(item.source_url);
    const f = item.featured;
    if (!f || !["selected","not_selected","pending"].includes(f.decision) || !Array.isArray(f.evidence) || !Array.isArray(f.uncertainties) || f.evidence.some((e) => typeof e.label !== "string" || typeof e.quote !== "string")) throw new Error("Invalid featured item");
    const c = item.classification;
    if (!c || c.version !== 2 || !["rules", "llm_reviewed"].includes(c.method) || !["not_analyzed", "reviewed"].includes(c.model_state) ||
        !Array.isArray(c.domains) || !c.domains.every((d) => payload.filter_options.domains.some((o) => o.id === d)) ||
        !payload.filter_options.types.some((t) => t.id === c.type) || !Array.isArray(c.topics) ||
        !c.topics.every((t) => payload.filter_options.topics.some((o) => o.id === t)) ||
        !payload.filter_options.sources.some((s) => s.id === item.publisher_id) || !Array.isArray(item.discovery_source_ids)) throw new Error("Invalid classification");
    const review = item.classification_review;
    if (!review || !["not_analyzed", "pending", "reviewed", "uncertain", "failed", "stale"].includes(review.state) || !review.rule || !Array.isArray(review.evidence) || typeof review.changed !== "boolean" || review.evidence.some((e) => typeof e.quote !== "string")) throw new Error("Invalid classification review");
    const scope = item.undergraduate;
    if (!scope || !["relevant", "excluded", "uncertain"].includes(scope.state) || !Array.isArray(scope.evidence) ||
        (scope.state === "relevant" && (typeof scope.summary !== "string" || !scope.summary)) ||
        (audience === "undergraduate" && (scope.state !== "relevant" || item.summary !== scope.summary))) throw new Error("Invalid undergraduate scope");
    if (guids.has(item.guid) || urls.has(item.source_url)) return false;
    guids.add(item.guid); urls.add(item.source_url); return true;
  });
  return payload;
}

function message(kind, title, copy) {
  $("notice-list").replaceChildren();
  $("message").hidden = false;
  $("message").dataset.kind = kind;
  $("message-title").textContent = title;
  $("message-copy").textContent = copy;
}

function clearData() {
  clearTimeout(expiryTimer);
  data = null;
  $("subscription-url").value = "";
  $("copy-subscription").disabled = true;
  $("subscription-link").hidden = true;
  $("subscription-link").removeAttribute("href");
  $("subscription-note").textContent = subscriptionHelp;
  $("collected-at").textContent = $("refreshed-at").textContent = "—";
  $("replay-note").hidden = true;
  $("featured-state").hidden = true;
  $("review-state").hidden = true;
  document.querySelectorAll("[data-count]").forEach((node) => { node.textContent = "—"; });
  $("source-list").replaceChildren(Object.assign(document.createElement("li"), {textContent: "当前未获取到可核验数据"}));
}

function unavailable(copy = "信息源暂时无法核验，已隐藏之前的通知。请稍后刷新。") {
  clearData();
  $("service-state").dataset.state = "error";
  $("service-state").lastChild.textContent = "信息暂不可用";
  $("result-count").textContent = "等待恢复";
  message("error", "校园信息暂不可用", copy);
}

function revalidate() {
  clearData();
  $("service-state").dataset.state = "loading";
  $("service-state").lastChild.textContent = "正在重新核验";
  $("result-count").textContent = "正在获取通知";
  message("loading", "正在获取校园信息", "页面恢复后会重新核验通知，请稍候。");
}

function card(item) {
  const fragment = $("notice-card").content.cloneNode(true);
  const find = (selector) => fragment.querySelector(selector);
  fragment.querySelector("article").dataset.guid = item.guid;
  fragment.querySelector("article").dataset.audience = audience;
  const time = find(".date-value");
  if (item.source_published_date) {
    time.dateTime = item.source_published_date;
    const day = Object.assign(document.createElement("span"), {className: "date-day", textContent: item.source_published_date.slice(5).replace("-", "/")});
    const year = Object.assign(document.createElement("span"), {className: "date-year", textContent: item.source_published_date.slice(0, 4)});
    time.append(day, year);
    time.setAttribute("aria-label", item.source_published_date);
  } else time.textContent = "日期未提供";
  find(".type-tag").textContent = data.filter_options.types.find((t) => t.id === item.classification.type)?.label || "未分类";
  find(".domain-tags").textContent = item.classification.domains.map((id) => data.filter_options.domains.find((t) => t.id === id)?.label).filter(Boolean).join(" · ");
  find(".topic-tags").textContent = item.classification.topics.map((id) => data.filter_options.topics.find((t) => t.id === id)?.label).filter(Boolean).join(" · ");
  find(".history-tag").hidden = item.listing !== "history";
  find(".status-tag").hidden = item.registration_status === "unknown";
  find(".status-tag").textContent = statuses[item.registration_status];
  find(".status-tag").dataset.state = item.registration_status;
  find(".notice-title").textContent = item.title;
  find(".source-name").textContent = "官方来源 · " + item.source_name;
  find(".summary").textContent = item.summary;
  const audienceNote = find(".audience-note");
  audienceNote.hidden = audience !== "undergraduate";
  audienceNote.textContent = "本科生办理要点 · 原文节选" + (item.listing === "history" ? " · 历史安排，先核对是否适用" : " · 年级、专业等具体资格以原文为准");
  const review = item.classification_review;
  const reviewLabels = {not_analyzed: "规则初分", pending: "等待模型复核", reviewed: review.changed ? "模型已纠正" : "模型已复核", uncertain: "复核依据不足", failed: "模型复核暂不可用", stale: "等待重新复核"};
  find(".classification-label").textContent = "分类依据 · " + reviewLabels[review.state];
  const describe = (c) => [data.filter_options.types.find((t) => t.id === c.type)?.label || "未分类", ...c.domains.map((id) => data.filter_options.domains.find((d) => d.id === id)?.label), ...c.topics.map((id) => data.filter_options.topics.find((t) => t.id === id)?.label)].filter(Boolean).join(" · ");
  find(".classification-basis").textContent = "规则初分：" + describe(review.rule) + (review.state === "reviewed" ? "；模型复核：" + describe(item.classification) : "");
  find(".classification-note").textContent = review.reason || "当前使用规则分类，模型未完成有效复核；分类仅供浏览。";
  find(".classification-evidence").replaceChildren(...review.evidence.map((e) => Object.assign(document.createElement("li"), {textContent: "原文依据：“" + e.quote + "”"})));
  for (const [selector, expanded] of [[".classification-reason", expandedClassifications], [".featured-reason", expandedFeatured]]) {
    const detail = find(selector);
    detail.open = expanded.has(item.guid);
    detail.querySelector("summary").addEventListener("click", () => {
      if (detail.open) expanded.delete(item.guid); else expanded.add(item.guid);
    });
    detail.addEventListener("toggle", () => {
      if (!detail.isConnected) return;
      if (detail.open) expanded.add(item.guid); else expanded.delete(item.guid);
    });
  }
  if (item.featured.decision === "selected") {
    find(".featured-reason").hidden = false;
    find(".featured-basis").textContent = "入选依据：" + item.featured.evidence.map((e) => e.label).join("、");
    find(".featured-evidence").replaceChildren(...item.featured.evidence.map((e) => Object.assign(document.createElement("li"), {textContent: "原文依据：“" + e.quote + "”"})));
    const unknowns = {eligibility_unknown: "参与资格待核实", registration_unknown: "报名状态未知", attachments_unread: "附件正文未读取"};
    const check = item.featured.reason_codes.includes("model_evidence") ? "经原文引用校验" : "经规则复核";
    find(".featured-boundary").textContent = "重要性是智能建议，" + check + "；摘要为原文节选，不代表官方重要性认定或当前可报名。" + item.featured.uncertainties.map((u) => unknowns[u]).filter(Boolean).join("；");
  }
  find(".checked-at").textContent = "系统采集 " + formatTime(item.source_checked_at);
  find(".checked-at").title = "最近成功采集：" + item.source_checked_at + "；首次收录：" + item.first_aggregated_at;
  find(".original-link").href = sourceUrl(item.source_url);
  find(".original-link").setAttribute("aria-label", "查看官方原文：" + item.title);
  find(".source-url").textContent = item.source_url;
  return fragment;
}

function render() {
  $("list-title").textContent = (audience === "undergraduate" ? "本科生实用 · " : "") + (selection === "featured" ? "重要精选 · " : "") + labels[view];
  selectionButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.selection === selection)));
  audienceButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.audience === audience)));
  $("view-note").textContent = view === "current" ? "仅列出有明确时间依据的可能参与机会；具体资格仍请核对官方原文。" :
    view === "history" ? "历史报道或明确已截止的信息；报名状态未知的历史通知不会标为已截止。" :
      "原文发布日期优先；日期未提供时，按首次收录时间排序。";
  viewButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
  if (!data) return;
  const subscription = new URL(data.subscription.url, location.origin);
  if (view !== "all") subscription.searchParams.set("view", view);
  if ($("subscription-url").value !== subscription.href) $("subscription-note").textContent = subscriptionHelp;
  $("subscription-url").value = subscription.href;
  $("subscription-link").href = subscription.href;
  $("subscription-link").hidden = false;
  $("copy-subscription").disabled = false;
  const featured = view === "all" ? data.featured : data.featured.ranges[view];
  $("featured-state").hidden = selection !== "featured";
  $("featured-state").dataset.state = featured.state;
  $("featured-state").textContent = featured.state === "degraded" ? "部分智能分析暂不可用。这里只展示当前有依据的精选；全部合格原文仍可在“全部信息”中查看。" :
    featured.state === "pending" ? "部分信息尚未分析或重要性依据不足，未入选不代表不重要。你可以切换“全部信息”查看完整通知。" :
      "精选按重要性建议与原文依据入选，与“当前机会”分别判断；个人相关性由下方筛选控制。";
  const count = (scope) => data.items.filter((item) => scope === "all" || item.listing === scope).length;
  document.querySelectorAll("[data-count]").forEach((node) => { node.textContent = count(node.dataset.count); });
  const items = data.items.filter((item) => view === "all" || item.listing === view);
  const reviewed = items.filter((item) => item.classification_review.state === "reviewed");
  const corrected = reviewed.filter((item) => item.classification_review.changed).length;
  const failed = items.filter((item) => item.classification_review.state === "failed").length;
  const uncertain = items.filter((item) => item.classification_review.state === "uncertain").length;
  const pending = items.length - reviewed.length - failed - uncertain;
  $("review-state").hidden = false;
  $("review-state").textContent = "模型复核 " + reviewed.length + " / " + items.length + " · 已纠正 " + corrected + (pending ? " · 待复核 " + pending : "") + (uncertain ? " · 依据不足 " + uncertain : "") + (failed ? " · 暂不可用 " + failed : "") + "。未完成复核的信息使用规则分类。";
  $("result-count").textContent = items.length + " 条信息";
  const detailSelectors = [".classification-reason", ".featured-reason"];
  const active = document.activeElement;
  const focusedGuid = active?.closest("article.notice-card")?.dataset.guid;
  const focusedSummary = detailSelectors.find((selector) => active?.matches(selector + " > summary"));
  $("notice-list").replaceChildren(...items.map(card));
  for (const node of $("notice-list").children) {
    if (node.dataset.guid === focusedGuid && focusedSummary) node.querySelector(focusedSummary + " > summary").focus({preventScroll: true});
  }
  $("message").hidden = items.length > 0;
  if (!items.length) {
    if (selection === "featured" && featured.state === "degraded") message("degraded", "智能精选暂时降级", "当前没有可展示的已核验精选；这不表示没有重要通知。请切换“全部信息”查看官方通知。");
    else if (selection === "featured" && featured.state === "pending") message("pending", featured.coverage.not_analyzed + featured.coverage.in_progress > 0 ? "精选尚未完成分析" : "精选依据仍待核实", "未分析或依据不足的信息仍保留在“全部信息”，不会被删除。");
    else if (Object.values(filterControls).some((id) => $(id).value && $(id).value !== "all")) message("empty", "这些条件下暂时没有通知", "可以减少筛选条件，或重置后查看本科生实用信息。未知状态不代表没有相关信息。");
    else if (view === "current") message("empty", "暂未确认当前可参与的机会", "你仍可在“最新通知”浏览校园信息。报名状态未确认，不代表已经截止。");
    else if (view === "history") message("empty", "暂时没有历史信息", "有符合历史条件的通知后，会在这里显示。");
    else if (audience === "undergraduate") message("empty", "暂未确认适合本科生的实用信息", "需要有学生办理事项或参与机会的原文依据；你仍可切换“全部原文”核对完整信息。");
    else message("empty", "暂时没有可展示的通知", "采集到通过质量检查的官方通知后，会自动出现在这里。");
  }
}

async function refresh() {
  clearTimeout(pollTimer);
  if (document.hidden) return;
  const ticket = ++generation;
  requestController?.abort();
  requestController = new AbortController();
  const controller = requestController;
  const timeout = setTimeout(() => controller.abort(), 6000);
  const started = performance.now();
  $("refresh").disabled = true;
  $("notice-list").setAttribute("aria-busy", "true");
  try {
    const response = await fetch(queryPath(), {cache: "no-store", credentials: "omit", signal: controller.signal, headers: {Accept: "application/json"}});
    if (response.status === 400) throw new Error("Invalid filters");
    if (!response.ok || !response.headers.get("content-type")?.startsWith("application/json")) throw new Error("Source unavailable");
    const payload = validate(await response.json());
    const remaining = payload.valid_for_ms - (performance.now() - started);
    if (remaining <= 0) throw new Error("Snapshot expired in transit");
    if (ticket !== generation || document.hidden) return;
    data = payload;
    populateFilters(payload);
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(() => unavailable("数据核验已过期，已隐藏通知。正在等待重新核验，请稍后刷新。"), remaining);
    $("service-state").dataset.state = "ready";
    $("service-state").lastChild.textContent = "官方来源 · 数据已核验";
    $("refreshed-at").textContent = formatTime(payload.refreshed_at);
    const latest = payload.items.map((i) => i.source_checked_at).sort((a,b) => Date.parse(b)-Date.parse(a))[0];
    $("collected-at").textContent = latest ? formatTime(latest) : "暂无采集记录";
    $("replay-note").hidden = payload.data_mode !== "offline_replay";
    const sources = [...new Set(payload.items.map((i) => i.source_name))].sort();
    $("source-list").replaceChildren(...(sources.length ? sources : ["暂无合格通知"]).map((name) => Object.assign(document.createElement("li"), {textContent: name})));
    render();
    pollTimer = setTimeout(refresh, Math.max(100, Math.min(5000, remaining / 2)));
  } catch (error) {
    if (ticket !== generation || document.hidden) return;
    console.warn("Notice page refresh failed", error.name, error.stack);
    unavailable(error.message === "Invalid filters" ? "筛选条件无效，请调整日期或重置筛选。" : undefined);
    pollTimer = setTimeout(refresh, 5000);
  } finally {
    clearTimeout(timeout);
    if (ticket === generation) {
      $("refresh").disabled = false;
      $("notice-list").setAttribute("aria-busy", "false");
    }
  }
}

viewButtons.forEach((button) => button.addEventListener("click", () => { view = button.dataset.view; render(); }));
selectionButtons.forEach((button) => button.addEventListener("click", () => { selection = button.dataset.selection; revalidate(); render(); refresh(); }));
audienceButtons.forEach((button) => button.addEventListener("click", () => { audience = button.dataset.audience; revalidate(); render(); refresh(); }));
Object.entries(filterControls).forEach(([key,id]) => $(id).addEventListener("change", () => {
  if (key === "published" && $(id).value === "unknown") {
    $("date-from").value = $("date-to").value = "";
  } else if ((key === "from" || key === "to") && $(id).value) $("date-presence").value = "known";
  $("date-from").disabled = $("date-to").disabled = $("date-presence").value === "unknown";
  revalidate(); refresh();
}));
$("reset-filters").addEventListener("click", () => {
  for (const [key,id] of Object.entries(filterControls)) $(id).value = ["from","to"].includes(key) ? "" : "all";
  $("date-from").disabled = $("date-to").disabled = false;
  view = "all"; selection = "all"; audience = "undergraduate"; revalidate(); render(); refresh();
});
$("refresh").addEventListener("click", refresh);
$("copy-subscription").addEventListener("click", async () => {
  const input = $("subscription-url");
  const value = input.value;
  if (!data || !value) return;
  try {
    await navigator.clipboard.writeText(value);
    if (data && input.value === value) $("subscription-note").textContent = "已复制当前筛选的订阅链接。链接仅在当前服务运行且可访问时有效。";
  } catch {
    if (data && input.value === value) {
      input.focus(); input.select();
      $("subscription-note").textContent = "请复制已选中的链接，再添加到 RSS 阅读器。链接仅在当前服务运行且可访问时有效。";
    }
  }
});
window.addEventListener("offline", () => { ++generation; requestController?.abort(); unavailable("网络已断开，通知暂时隐藏。网络恢复后会重新获取。"); });
window.addEventListener("online", refresh);
document.addEventListener("visibilitychange", () => {
  ++generation; requestController?.abort(); clearTimeout(pollTimer);
  revalidate();
  if (!document.hidden) refresh();
});
window.addEventListener("pagehide", () => { ++generation; requestController?.abort(); revalidate(); });
window.addEventListener("pageshow", () => { if (!data) refresh(); });
