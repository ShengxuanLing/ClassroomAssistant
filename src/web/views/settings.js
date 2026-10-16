/*
 * 设置页 (``#/settings``, Freebuff 式: 左侧分类 + 右侧卡片)。
 *
 * 设计参考 Freebuff 的设置页: 左列分类导航 (通用 / 智能 / 系统), 右列一张张
 * 卡片, 每卡一个主题, 开关走右侧的 switch。
 *
 * 三个分区:
 *   general —— 界面语言 (浏览器侧状态, 经 setLang 入口; 这里是全站唯一的
 *              语言切换 UI —— 顶栏的 #ui-language 选择器已于 2026-10-16
 *              全站删除);
 *   ai      —— 本地 AI 开关 + 生效状态 (provider / mode 来自 /api/settings
 *              的 workspace 快照, 不是设置文件里的愿望值);
 *   system  —— 开机自启 (Windows 注册表) + 自动检查更新 + 版本 + 立即检查。
 *
 * 持久化只有后两个分区走 ``PATCH /api/settings``; 语言保持 localStorage
 * (``ca.lang``) —— 它是"这个浏览器看什么语言", 服务端持久化反而会让多浏览器
 * 的行为不确定。真相源分工写在 settings.py 的模块 docstring 里。
 */
'use strict';

//: 设置页分区白名单。hash 里给了白名单之外的段 -> 回落 'general'
//: (绝不渲染一个空页面, 也绝不因为深链里一个拼错的分区就报错)。
const SETTINGS_SECTIONS = ['general', 'ai', 'system'];

//: AI mode -> 状态说明的 i18n key。``mode`` 是后端真相 (disabled / fake /
//: real / manual), 说明文字刻意放在前端三语表里 —— 模式取值是技术标识,
//: 解释是界面文案, 两者不该混。
function aiStatusKey(mode) {
  if (mode === 'fake') return 'settings.ai.status.fake';
  if (mode === 'real') return 'settings.ai.status.real';
  if (mode === 'manual') return 'settings.ai.status.manual';
  return 'settings.ai.status.disabled';
}

//: 设置分区的导航文案。刻意用 if 链而不是按名字拼 key 的动态前缀: 本项目的
//: i18n 守卫对动态前缀要求登记取值域 (见 support.py 的 dynamic_i18n_prefixes),
//: 而三个分区的取值域就是 SETTINGS_SECTIONS 这份清单本身 —— 抄一份去注册处
//: 等于把规则抄两遍。与 review.js 的 rsBucketLabel 同一手法。
function settingsNavLabel(name) {
  if (name === 'general') return t('settings.nav.general');
  if (name === 'ai') return t('settings.nav.ai');
  return t('settings.nav.system');
}

function settingsNavHtml(section) {
  // aria-current="page" 是给读屏软件的"当前分区" (2026-10-16 P1-3);
  // .active 只管视觉, 读屏听不到。
  return SETTINGS_SECTIONS.map((name) => {
    const attrs = name === section ? ' class="active" aria-current="page"' : '';
    return '<a' + attrs + ' href="#/settings/' + name + '">' +
      settingsNavLabel(name) + '</a>';
  }).join('');
}

function settingsGeneralHtml(snap) {
  // 语言按钮: 三个端名 (中文 / Español / Català) 是**专有名词**, 三语界面
  // 下都一样, 不做翻译 (与已删除的顶栏 #ui-language <option> 同口径)。
  const buttons = ['zh', 'es', 'ca'].map((lang) => {
    const label = lang === 'zh' ? '中文' : (lang === 'es' ? 'Español' : 'Català');
    // active 并进同一个 class 属性 —— 写成第二个 class="..." 是非法 HTML,
    // 浏览器只认第一个, 选中样式会静默失效 (2026-10-15 预览快照实测抓到)。
    // aria-pressed 表达分段控件的"选中"状态: 只有视觉 class 的话, 读屏
    // 听到的是三个无状态按钮 (2026-10-16 P1-3)。
    const selected = state.lang === lang;
    return '<button type="button" class="lang-btn' + (selected ? ' active' : '') +
      '" aria-pressed="' + (selected ? 'true' : 'false') +
      '" data-lang="' + lang + '">' + esc(label) + '</button>';
  }).join('');
  return '<section class="settings-card">' +
    '<h2>' + t('settings.general.language') + '</h2>' +
    '<p class="settings-desc">' + t('settings.general.languageHint') + '</p>' +
    '<div class="lang-seg">' + buttons + '</div>' +
    '</section>';
}

function settingsAiHtml(snap) {
  const ai = snap.ai || {};
  const mode = String(ai.mode || 'disabled');
  const enabled = Boolean(ai.enabled);
  const provider = String(ai.provider || 'none');
  const envDefault = Boolean(ai.env_default_enabled);
  // 开关显示的是**生效状态** (workspace 内存真相), 不是设置文件里的
  // local_ai_enabled (它可能是 null = 跟随环境)。用户一拨动, PATCH 就把它
  // 变成显式的 true/false。
  const checked = enabled ? ' checked' : '';
  const statusLine =
    '<div class="settings-row"><span class="settings-row-label">' +
    t('settings.ai.statusLabel') + '</span>' +
    '<span class="settings-row-note">' + t(aiStatusKey(mode)) + '</span></div>' +
    '<div class="settings-row"><span class="settings-row-label">' +
    t('settings.ai.providerLabel') + '</span>' +
    '<span class="settings-row-note mono">' + esc(provider) + '</span></div>' +
    '<div class="settings-row"><span class="settings-row-label">' +
    t('settings.ai.envLabel') + '</span>' +
    // 环境默认值 (LOOMIND_AI_ENABLED) 是技术开关, 原样显示 true/false ——
    // 与 provider 行同一口径: 环境事实不做措辞包装。
    '<span class="settings-row-note mono">' + (envDefault ? 'true' : 'false') + '</span></div>';
  // 没有 provider 时给一条诚实提示: 开启也不会调用真实模型。
  const noProvider = (!enabled && provider === 'none')
    ? '<p class="settings-row-note">' + t('settings.ai.noProvider') + '</p>'
    : '';
  return '<section class="settings-card">' +
    '<h2>' + t('settings.ai.title') + '</h2>' +
    '<p class="settings-desc">' + t('settings.ai.desc') + '</p>' +
    // checkbox 的可访问名: label 只包 aria-hidden 的视觉轨道, 可见文字是
    // 兄弟 span —— 用 aria-labelledby 关联 (2026-10-16 P1-3; label for
    // 也行, 但 input 在 switch label 内, 双 label 会歧义)。
    '<div class="settings-row"><span class="settings-row-label" id="label-local-ai">' +
    t('settings.ai.toggle') + '</span>' +
    '<label class="switch"><input type="checkbox" id="toggle-local-ai"' +
    ' aria-labelledby="label-local-ai"' + checked + '>' +
    '<span class="switch-track" aria-hidden="true">' +
    '<span class="switch-knob"></span></span></label></div>' +
    statusLine + noProvider +
    '</section>';
}

function settingsSystemHtml(snap) {
  const auto = snap.autostart || {};
  const supported = Boolean(auto.supported);
  const enabled = Boolean(auto.enabled);
  const autoChecked = supported && enabled ? ' checked' : '';
  const autoDisabled = supported ? '' : ' disabled';
  const autoNote = supported
    ? ''
    : '<p class="settings-row-note">' + t('settings.system.autostartUnsupported') + '</p>';
  const autoRow =
    '<div class="settings-row"><span class="settings-row-label" id="label-autostart">' +
    t('settings.system.autostart') + '</span>' +
    '<label class="switch"><input type="checkbox" id="toggle-autostart"' +
    ' aria-labelledby="label-autostart"' + autoChecked + autoDisabled + '>' +
    '<span class="switch-track" aria-hidden="true">' +
    '<span class="switch-knob"></span></span></label></div>';
  const update = snap.auto_update || {};
  const updateChecked = update.enabled === false ? '' : ' checked';
  return '<section class="settings-card">' +
    '<h2>' + t('settings.system.autostart') + '</h2>' +
    '<p class="settings-desc">' + t('settings.system.autostartDesc') + '</p>' +
    autoRow + autoNote +
    '</section>' +
    '<section class="settings-card">' +
    '<h2>' + t('settings.system.autoUpdate') + '</h2>' +
    '<p class="settings-desc">' + t('settings.system.autoUpdateDesc') + '</p>' +
    '<div class="settings-row"><span class="settings-row-label" id="label-auto-update">' +
    t('settings.system.autoUpdate') + '</span>' +
    '<label class="switch"><input type="checkbox" id="toggle-auto-update"' +
    ' aria-labelledby="label-auto-update"' + updateChecked + '>' +
    '<span class="switch-track" aria-hidden="true">' +
    '<span class="switch-knob"></span></span></label></div>' +
    '<div class="settings-row"><span class="settings-row-label">' +
    t('settings.system.version') + '</span>' +
    '<span class="settings-row-note mono">v' + esc(snap.version || '?') + '</span>' +
    '<button type="button" class="primary" id="check-update">' +
    t('settings.system.checkNow') + '</button></div>' +
    '<div id="update-result" class="update-result" aria-live="polite"></div>' +
    '</section>';
}

function settingsPageHtml(section, snap) {
  const body = section === 'ai' ? settingsAiHtml(snap)
    : (section === 'system' ? settingsSystemHtml(snap) : settingsGeneralHtml(snap));
  return '<div class="settings-layout">' +
    '<nav class="settings-nav" aria-label="' + t('settings.title') + '">' +
    settingsNavHtml(section) + '</nav>' +
    '<div class="settings-body"><h1>' + t('settings.title') + '</h1>' + body + '</div>' +
    '</div>';
}

//: 当前设置页的最新快照 (开关 PATCH 成功后更新, 用于重渲染状态行)。
let __settingsSnap = null;

function settingsRenderUpdateResult(text, href) {
  const el = document.getElementById('update-result');
  if (!el) return;
  el.innerHTML = href
    ? esc(text) + ' <a href="' + esc(href) + '" target="_blank" rel="noopener">' +
      t('settings.system.gotoDownload') + '</a>'
    : esc(text);
}

/**
 * 拨动一个开关 -> PATCH -> 失败回滚 checkbox + toast。
 *
 * 回滚不是装饰: checkbox 的 checked 在 change 事件里**已经**变了, 失败后不
 * 还原的话界面显示"已开启"而系统还是关的 —— 与项目里"改了 state 没改显示"
 * 是同一族缺陷。
 */
function wireSettingToggle(id, key) {
  const box = document.getElementById(id);
  if (!box || !box.addEventListener) return;
  box.addEventListener('change', () => {
    const value = box.checked;
    api('/settings', { method: 'PATCH', body: buildSettingsPatch(key, value) })
      .then((updated) => {
        __settingsSnap = updated;
      })
      .catch((err) => {
        box.checked = !value;
        toast(t('settings.saveFailed') + ': ' + (err && err.message ? err.message : err), 'bad');
      });
  });
}

//: 构造 PATCH 体 (单键)。独立成函数而不是内联对象字面量: 计算键名
//: ``{ [key]: value }`` 在审计的语法检查里不如显式 concat 稳。
function buildSettingsPatch(key, value) {
  const patch = {};
  patch[key] = value;
  return patch;
}

function wireSettingsPage(section, snap) {
  __settingsSnap = snap;
  if (section === 'general') {
    document.querySelectorAll('.lang-btn').forEach((btn) => {
      if (!btn.addEventListener) return;
      btn.addEventListener('click', () => {
        setLang(btn.getAttribute('data-lang'));
        // 语言切换后重绘当前页 (hash 不变, 仍是 #/settings/<section>)。
        applyI18n();
        route();
      });
    });
  }
  if (section === 'ai') {
    wireSettingToggle('toggle-local-ai', 'local_ai_enabled');
  }
  if (section === 'system') {
    wireSettingToggle('toggle-autostart', 'autostart');
    wireSettingToggle('toggle-auto-update', 'auto_update_check');
    const btn = document.getElementById('check-update');
    if (btn && btn.addEventListener) {
      btn.addEventListener('click', () => {
        btn.disabled = true;
        settingsRenderUpdateResult(t('settings.system.checking'), null);
        api('/settings/update-check', { method: 'POST' })
          .then((result) => {
            btn.disabled = false;
            if (!result || result.ok !== true) {
              settingsRenderUpdateResult(
                t('settings.system.checkFailed', { e: (result && result.error) || 'unknown' }),
                null);
              return;
            }
            if (result.update_available) {
              settingsRenderUpdateResult(
                t('settings.system.updateAvailable', { v: result.latest }),
                result.release_url);
            } else {
              settingsRenderUpdateResult(t('settings.system.upToDate'), null);
            }
          })
          .catch((err) => {
            btn.disabled = false;
            settingsRenderUpdateResult(
              t('settings.system.checkFailed', { e: (err && err.message) || err }), null);
          });
      });
    }
  }
}

async function pageSettings(section) {
  markActiveNav('#/settings');
  showLoading();
  let snap;
  try {
    snap = await api('/settings');
  } catch (err) {
    renderError(err);
    return;
  }
  const active = SETTINGS_SECTIONS.indexOf(section) >= 0 ? section : 'general';
  setView(settingsPageHtml(active, snap));
  wireSettingsPage(active, snap);
}
