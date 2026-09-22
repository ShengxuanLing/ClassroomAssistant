/*
 * 概览页 (``#/``) 与考前复习模式 (``#/today``)。
 *
 * 页面层级 (2026-09-22, 任务书 §2):
 *   概览 / 今日 = **全局页** —— 默认按**全部课程**聚合, 不跟随左侧选中的
 *   当前课程; 顶栏选择器在这两页上只是本页的**查看范围**筛选 (state.scope)。
 *   选一门课时给出单课程视图, 回到「全部课程」时恢复聚合。
 */
'use strict';

// ---------------------------------------------------------------- 聚合层
//
// 为什么聚合只吃 ``/api/my-courses`` 的行 (任务书 §14): 后端每行已经是
// **带 course_id 的独立查询** (multi_course.py: "没有一处是全局查再筛"),
// 汇总就是把这些真实数字相加。前端**不再**为聚合重复调用单课程接口,
// 也绝不硬编码任何数字。

/** 聚合图表的列。取值域 = 后端 COUNT_KEYS 的展示子集 (概览页口径)。 */
const OV_STAT_KEYS = [
  ['sessions', '课堂'], ['materials', '材料'], ['knowledge_points', '知识点'],
  ['pending_review', '待审核'], ['students', '学生'], ['exercises', '练习'],
];

/** 把 my-courses 的课程行 + totals 聚合成概览页需要的形状。 */
function aggregateDashboardData(myCourses) {
  const rows = (myCourses.courses || []).slice().sort((a, b) =>
    String(a.course_id).localeCompare(String(b.course_id)));
  const totals = myCourses.totals || {};
  return {
    courseCount: (myCourses.courses || []).length,
    rows: rows,
    // 优先用后端算好的 totals (单一事实源); 后端没给时才在现有行上求和 ——
    // 两条路都只消费真实数据, 没有任何一条是"编出来"的。
    totals: {
      counts: (totals.counts && Object.keys(totals.counts).length)
        ? totals.counts
        : OV_STAT_KEYS.reduce((acc, entry) => {
            acc[entry[0]] = rows.reduce(
              (sum, row) => sum + Number(((row.counts || {})[entry[0]]) || 0), 0);
            return acc;
          }, {}),
      evidence_total: Number(totals.evidence_total || 0),
      gaps: Number(totals.gaps || 0),
    },
  };
}

/** 概览的一张统计格。 */
function ovStat(label, value, hint) {
  return '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div>' +
    (hint ? '<div class="stat-hint">' + esc(hint) + '</div>' : '') + '</div>';
}

/** 课程概况的一行: 课程名 + 关键计数徽章 (任务书 §4 的形状)。 */
function ovCourseRow(row, isCurrent) {
  const counts = row.counts || {};
  return '<a class="ov-row' + (isCurrent ? ' ov-row-current' : '') +
    '" href="#/courses/' + encodeURIComponent(row.course_id) + '">' +
    '<div class="ov-row-head"><strong>' + esc(row.name || row.code || '') + '</strong>' +
    (isCurrent ? ' <span class="pill pill-ok">' + esc(t('mc.current')) + '</span>' : '') +
    '<span class="tiny muted">' + esc(t('mc.count.sessions')) + ' ' +
    esc(fmtNumber(counts.sessions || 0)) + '</span></div>' +
    '<div class="ov-row-counts">' +
    ['materials', 'knowledge_points', 'pending_review', 'exercises']
      .map((key) => '<span class="pill pill-muted">' + esc(t('mc.count.' + key)) + ' ' +
        esc(fmtNumber(counts[key] || 0)) + '</span>')
      .join(' ') +
    '</div></a>';
}

/** 全部课程口径的概览页 (任务书 §3-§4)。 */
async function pageDashboardGlobal() {
  const data = await api('/my-courses', {
    query: { lang: state.lang, preferred: state.courseId || undefined },
  });
  const agg = aggregateDashboardData(data);
  const counts = agg.totals.counts;

  setView(
    '<div class="page-head"><h1>' + t('概览') + '</h1>' +
    '<p class="subtitle">' + esc(t('scope.all')) + ' · ' +
    esc(agg.courseCount) + ' ' + esc(t('ov.coursesUnit')) + '</p>' +
    '</div>' +

    '<div class="grid grid-4" style="margin-bottom:16px">' +
    OV_STAT_KEYS.map((entry) =>
      ovStat(t('mc.count.' + entry[0]), fmtNumber(counts[entry[0]] || 0))).join('') +
    ovStat(t('mc.gaps'), fmtNumber(agg.totals.gaps || 0)) +
    ovStat(t('mc.evidence'), fmtNumber(agg.totals.evidence_total || 0)) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + esc(t('ov.overview')) + '</h2>' +
    '<a class="small" href="#/courses">' + esc(t('mc.open')) + ' →</a></div>' +
    (agg.rows.length
      ? agg.rows.map((row) =>
          ovCourseRow(row, row.course_id === state.courseId)).join('')
      : emptyState(t('mc.empty'))) +
    '<p class="tiny muted">' + esc(t('ov.summaryNote')) + '</p>' +
    '</div>'
  );
}

/** 单课程口径的概览页 (state.scope 指到一门课时的筛选视图, 任务书 §6)。 */
async function pageDashboardCourse(courseId) {
  const data = await api('/dashboard', { query: { course_id: courseId } });
  if (data.course_id && data.course_id !== state.courseId) setCourse(data.course_id);
  pageDashboardCourseBody(data);
}
async function pageDashboard() {
  markActiveNav('#/');
  // 概览是全局页: 默认 "全部课程"; 顶栏选择器 (state.scope) 可以把它筛成
  // 单课程。scope 指向的课程必须还在课程列表里, 否则回退到全部 —— 失效的
  // 筛选不应该是"卡住的页面"。
  const scope = currentScope();
  if (scope && __courseCache.length &&
      !__courseCache.some((c) => c.course_id === scope)) {
    setScope('all');
  }
  const effective = currentScope();
  if (!effective) {
    if (!__courseCache.length) {
      // 课程列表还没加载 (loadSidebar 之前 / 失败): 退回"当前课程"口径的旧
      // 请求, 仍然能渲染出有意义的一屏, 而不是空白或报错。
      await pageDashboardCourse(state.courseId);
      return;
    }
    await pageDashboardGlobal();
    return;
  }
  await pageDashboardCourse(effective);
}

/**
 * 单课程概览的**渲染体** (入参 = ``/api/dashboard`` 的响应)。
 *
 * 与取数分开成两个函数: ``pageDashboardCourse()`` 从 scope 筛选进入时,
 * 课程列表已经加载过; 而 route() 首屏 (loadSidebar 之前的兜底) 也可能直接
 * 拿着当前课程进来 —— 两种入口共用同一个渲染, 只有一次取数。
 */
function pageDashboardCourseBody(data) {
  if (!data.courses.length) {
    // 零课程 = 新用户的第一屏。
    //
    // 这里**不再**把 curl 命令教给用户 (旧版直接贴出 POST /api/courses 的
    // 原始请求体)。创建入口已经长在「我的课程」页上 (courseForm), 空态只
    // 负责把人送过去 —— 新用户拿到这个应用必须能**在网页上**开始使用。
    setView(
      '<div class="card"><h1>' + t('课堂助手') + '</h1>' +
      '<p class="muted">' + t('还没有任何课程。先创建一门课程，再建课堂、上传材料。') + '</p>' +
      '<p><a class="btn" href="#/courses">' + t('新建课程') + '</a></p>' +
      '</div>'
    );
    return;
  }

  // ---- dashboard 专用的"说明行"渲染 ------------------------------------
  //
  // 全局 warningRow() / zeroEvidenceHint() 用 <br> 把说明文字塞在 pill 后面,
  // dashboard 材料表是 table-layout: fixed, 状态列被钉死在 160px —— 说明约
  // 150 字符要折 5~8 行, 把整行撑成一根细长条。这里改用一个**独立的说明行**
  // (colspan=3, 占满卡宽, 只折 1~2 行), 由调用处挂在对应材料行下面。
  //
  // 这里只换容器 (用 .material-detail 块级 + 与 pill 的间距), 数据来源 /
  // 文案映射**全部走同一个 warningText() / 同一份 t() 表**, 不会产生第二份
  // 文案真源。其它页 (materials / session) 是非 fixed 表格、列宽自动分配,
  // 继续用全局版, 不动它们。
  function dashboardWarnRow(m) {
    const code = (m || {}).warning;
    if (!code) return '';
    const text = warningText(code);
    return '<div class="material-detail">' +
      '<span class="tiny pill pill-warn">' + esc(code) + '</span>' +
      (text && text !== code ? ' <span>' + esc(text) + '</span>' : '') +
      '</div>';
  }
  function dashboardEmptyRow(m) {
    const record = m || {};
    const status = String(record.status || record.processing_status || '').toUpperCase();
    if (status !== 'COMPLETED' && status !== 'SUCCEEDED') return '';
    if (evidenceCountOf(record) !== 0) return '';
    let detail = '';
    switch (record.warning) {
      case 'NO_TEXT_EXTRACTED': detail = t('warn.empty.NO_TEXT_EXTRACTED'); break;
      case 'NO_TEXT_DETECTED': detail = t('warn.empty.NO_TEXT_DETECTED'); break;
      case 'TRANSCRIPT_QUALITY_INVALID': detail = t('warn.empty.TRANSCRIPT_QUALITY_INVALID'); break;
      case 'TRANSCRIPT_QUALITY_WARNING': detail = t('warn.empty.TRANSCRIPT_QUALITY_WARNING'); break;
      default: detail = '';
    }
    return '<div class="material-detail">' + esc(t('warn.emptySuccessHint')) +
      (detail ? ' ' + esc(detail) : '') + '</div>';
  }

  const courseId = data.course_id;
  const summary = data.knowledge.summary || {};
  const validation = summary.validation_summary || {};
  const review = summary.review_summary || {};
  const coverage = summary.coverage || {};
  const gaps = (data.gaps && data.gaps.gaps) || [];
  const counts = data.processing.by_status || {};
  const activeCounts = Object.keys(counts).sort().filter((k) => counts[k] > 0);

  const stat = (label, value, hint) =>
    '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div>' +
    (hint ? '<div class="stat-hint">' + esc(hint) + '</div>' : '') + '</div>';

  const jobs = (data.processing.jobs || []).slice(0, 8);
  const materials = (data.materials || []).slice(0, 8);

  setView(
    '<div class="page-head">' +
    '<h1>' + t('概览') + '</h1>' +
    '<p class="subtitle">' +
    // scope 筛选视图: 明确这是"某一门课"的概览, 并给出回到全部的出口。
    (currentScope()
      ? esc(courseLabel(courseId)) + ' · <a href="#/">' + esc(t('scope.viewAll')) + '</a>'
      : '') +
    esc(data.courses.length) + t(' 门课程 · v') + esc(data.version) + '</p>' +
    '</div>' +

    // 前 7 张卡都是**当前课程**口径 (它们的查询都带 course_id), 只有最后一张
    // 「全部课程」是全局口径 —— 所以它单独放到队尾, 且标签必须写明"全部"。
    // 旧写法把它放在第一位、标签只写「课程」, 用户读到的是"这门课有 5 个课程"。
    '<div class="grid grid-4" style="margin-bottom:16px">' +
    stat(t('课堂'), (data.sessions || []).length) +
    stat(t('材料'), (data.materials || []).length) +
    stat(t('知识点'), data.knowledge.count) +
    stat(t('待审核'), (data.review_pending || []).length) +
    stat(t('学生'), (data.students || []).length) +
    stat(t('练习'), (data.exercises || []).length) +
    stat(t('学习缺口'), gaps.length) +
    stat(t('today.allCourses'), data.courses.length) +
    '</div>' +

    // 两两并排 + 「材料」独占整行: 4 列表格在 ~270px 卡宽里会把文件名按字符竖排
    // (实测截过 Thr/ee/Ho/riz/ons), pill / 0/3 也跟着折。让内容最多的「材料」表
    // 吃满一行, 另两张简表并排, 既能塞下 3 列也不浪费空间。
    '<div class="grid grid-2">' +

    '<div class="card"><div class="card-head"><h2>' + t('处理状态') + '</h2>' +
    '<a class="small" href="#/materials">' + t('全部材料 →') + '</a></div>' +
    (activeCounts.length
      ? '<p class="row small">' + activeCounts
          .map((k) => pill(k + ' ' + counts[k], k)).join('') + '</p>'
      : '') +
    (jobs.length
      ? '<table class="data compact fixed">' + tableCaption(t('处理状态')) +
        '<colgroup><col><col style="width:160px"><col class="num" style="width:64px"></colgroup>' +
        '<thead><tr><th scope="col">' + t('材料') + '</th><th scope="col">' + t('阶段') + '</th>' +
        '<th scope="col" class="num">' + t('尝试') + '</th></tr></thead><tbody>' +
        jobs.map((job) => (
          // 阶段 + 状态是同一个 pill: `pill(label, colorKind)`, 旧写法把同一个
          // status 又画了一列, 在窄格里被拆成两行 ("0/" + "3")。现在合并。
          '<tr><td class="ellipsis mono tiny" title="' + esc(job.material_id) + '">' + esc(job.material_id) + '</td>' +
          '<td>' + pill(job.stage, job.status) + '</td>' +
          '<td class="num">' + esc(job.attempts) + '/' + esc(job.max_attempts) + '</td></tr>'
        )).join('') + '</tbody></table>'
      : emptyState(t('还没有处理任务。上传材料后点击"处理"。'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('知识健康度') + '</h2>' +
    '<a class="small" href="#/knowledge">' + t('知识点 →') + '</a></div>' +
    // 三组按"是什么"切: 结构 (知识点/主题/关系/覆盖) / 证据支持 (val.*) /
    // 人工审核 (rev.*)。**不**把数字合并成"总分" —— 两条真相轴
    // (证据 vs 人工) 必须分开, 与「我的课程」同款语义。
    //
    // "覆盖" (``coverage.assigned``) 并进"结构"组而不是单独成组: 它只有
    // 一个指标, 单独成组会让组名与字段名同名 —— 实测 zh 渲染出
    // "覆盖 / 覆盖 12"、es 渲染出 "Cobertura / Cobertura 12", 看起来像
    // 渲染 bug。语义上"有多少知识点装配进了课程结构"本来就属于"结构"。
    //
    // .kv-groups 是**两列网格** (见 styles.css): 单列分组仍会把标签与数字
    // 贴在卡片左边、右侧空一大片 —— 用户报的"全挤在左边"没解决。两列之后
    // 内容铺满卡宽; 窄屏 (<=560px) 自动收成一列。
    '<div class="kv-groups">' +
    '<div class="kv-group">' +
    '<div class="kv-group-label">' + t('dashboard.health.structure') + '</div>' +
    '<dl class="kv compact">' +
    '<dt>' + t('知识点') + '</dt><dd>' + esc(summary.knowledge_point_count || 0) + '</dd>' +
    '<dt>' + t('主题') + '</dt><dd>' + esc(summary.topic_count || 0) + '</dd>' +
    '<dt>' + t('关系') + '</dt><dd>' + esc(summary.relation_count || 0) + '</dd>' +
    '<dt>' + t('覆盖') + '</dt><dd>' + esc(coverage.assigned !== undefined ? coverage.assigned : '—') + '</dd>' +
    '</dl></div>' +
    '<div class="kv-group">' +
    '<div class="kv-group-label">' + t('dashboard.health.validation') + '</div>' +
    '<dl class="kv compact">' +
    '<dt>' + t('val.supported') + '</dt><dd>' + esc(validation.validated || 0) + '</dd>' +
    '<dt>' + t('val.unverified') + '</dt><dd>' + esc(validation.unverified || 0) + '</dd>' +
    '<dt>' + t('val.conflicted') + '</dt><dd>' + esc(validation.conflicted || 0) + '</dd>' +
    '</dl></div>' +
    '<div class="kv-group">' +
    '<div class="kv-group-label">' + t('dashboard.health.review') + '</div>' +
    '<dl class="kv compact">' +
    '<dt>' + t('rev.confirmed') + '</dt><dd>' + esc(review.confirmed || 0) + '</dd>' +
    '<dt>' + t('rev.pending') + '</dt><dd>' + esc(review.pending || 0) + '</dd>' +
    '</dl></div>' +
    '</div>' +
    '</div>' +

    '</div>' +

    // 材料表独占整行 —— 4 列(文件 / 类型 / 状态 / 大小)在窄格里被挤坏;
    // 现在去掉「类型」列(扩展名本已自带, 旧列把 "text" 拆成 "te"/"xt"),
    // 类型作为小字后缀挂在文件名后面, 既保留信息又不占一整列。
    '<div class="card"><div class="card-head"><h2>' + t('材料') + '</h2>' +
    '<a class="small" href="#/materials">' + t('全部 →') + '</a></div>' +
    (materials.length
      ? '<table class="data compact fixed">' + tableCaption(t('材料')) +
        '<colgroup><col><col style="width:160px"><col class="num" style="width:80px"></colgroup>' +
        '<thead><tr><th scope="col">' + t('文件') + '</th><th scope="col">' + t('状态') + '</th>' +
        '<th scope="col" class="num">' + t('大小') + '</th></tr></thead><tbody>' +
        materials.map((m) => {
          // 文件名单行 + ellipsis, 全文走 title 浮层; 类型作小字后缀挂在
          // 文件名后, 用 "·" 分隔 —— 直接空格相接时 "x.pdf text" 看起来像
          // 文件名的一部分, 分隔符让"类型"读起来是标注而不是名字。
          //
          // D1 警告 / D4 零证据提示放在**独立的说明行** (colspan=3), 而不是
          // 塞进状态列。原因: 这张表是 table-layout: fixed, 状态列被钉死在
          // 160px, 而那段说明约 150 字符 —— 在 160px 里要折 5~8 行, 把整行
          // 撑成一根细长条 (用户截图里的形状)。占满卡宽之后只折 1~2 行。
          // 状态列因此只放状态 pill, 回到干净的单行。
          //
          // 文案来源与判据**完全不变** (dashboardWarnRow / dashboardEmptyRow
          // 仍走全局的 warningText() / evidenceCountOf() / 同一份 t() 表)。
          // 材料页用的是非 fixed 表格 (列宽自动分配), 状态列不会被钉死,
          // 所以那边维持原来的 <br> 写法不动。
          const note = dashboardWarnRow(m) + dashboardEmptyRow(m);
          return '<tr' + (note ? ' class="material-row"' : '') + '>' +
            '<td class="ellipsis" title="' + esc(m.filename) + '">' + esc(m.filename) +
            (m.duplicate ? ' <span class="pill pill-info">' + t('重复') + '</span>' : '') +
            ' <span class="tiny muted">· ' + esc(m.material_type || m.source_type || '—') + '</span>' +
            '</td>' +
            '<td>' + pill(m.processing_status) + '</td>' +
            '<td class="num">' + fmtBytes(m.size) + '</td></tr>' +
            (note ? '<tr class="material-note"><td colspan="3">' + note + '</td></tr>' : '');
        }).join('') + '</tbody></table>'
      : emptyState(t('该课程还没有材料。'))) +
    '</div>' +

    '<div class="grid grid-2">' +

    '<div class="card"><div class="card-head"><h2>' + t('待审核') + '</h2>' +
    '<a class="small" href="#/reviews">' + t('全部 →') + '</a></div>' +
    ((data.review_pending || []).length
      ? '<ul class="small">' + data.review_pending.slice(0, 6).map((item) => {
          const kpId = item.knowledge_point_id || item.knowledge_id;
          return '<li><a href="#/courses/' + encodeURIComponent(courseId) +
            '/knowledge/' + encodeURIComponent(kpId) + '">' + esc(kpId) + '</a> ' +
            pill(item.reason || item.decision || 'PENDING', 'PENDING') + '</li>';
        }).join('') + '</ul>'
      : emptyState(t('没有待审核的知识点。'))) +
    '</div>' +

    '<div class="card"><div class="card-head"><h2>' + t('学习缺口') + '</h2></div>' +
    (gaps.length
      ? '<ul class="small">' + gaps.slice(0, 8).map((gap) =>
          '<li>' + pill(gap.gap_type || gap.type || 'GAP', 'PENDING') + ' ' +
          esc(gap.description || gap.knowledge_point_id || gap.topic_id || '') + '</li>'
        ).join('') + '</ul>'
      : emptyState(t('未检测到缺口。'))) +
    '</div>' +

    '</div>'
  );
}

// ---- 今天的学习流程 (Task 66) ---------------------------------------------
//
// 「开始今天的学习」入口把既有链路串起来:
//   Today -> StudyPlan -> LearningPath -> Knowledge -> Exercise -> Answer
//   -> Evaluation -> StudentState
//
// 这个页面的**唯一职责是如实显示**。几条不可让步的约束:
//
// 1. **前端不挑任务。** 学什么由后端 `/learning/start` 的确定性排序决定;
//    这里绝不按 correct_count / wrong_count 重排, 更不出现"掌握度"文案。
// 2. **两个 truth axis 分开显示。** validation_status（证据支持度）与
//    review_status（人工审核状态）是两个独立事实, 绝不合并成一个徽章。
// 3. **计数不是掌握度。** 显示 answer_count / correct_count 是**事实**,
//    旁边必须有一句说明, 且不得出现"你已掌握"这类结论。
// 4. **答题不推进状态。** 提交后如实显示后端返回的 student_state,
//    不在前端自己把"答对 3 次"渲染成"已掌握"。
// 5. **无证据就说无证据。** evidence_available=false 时显示
//    "Evidence unavailable.", 不编造解释。


async function pageToday() {
  markActiveNav('#/today');
  // 今日 = 全局页 (2026-09-22): 默认**不传 course_id** —— 后端按全部课程聚合
  // (student_today_view.py 的既有能力); state.scope 只作本页筛选 (任务书 §11)。
  // scope 失效 (课程被删) 时归位到全部, 而不是卡在 404 上。
  const scope = currentScope();
  if (scope && __courseCache.length &&
      !__courseCache.some((c) => c.course_id === scope)) {
    setScope('all');
  }
  const effective = currentScope();
  const query = {};
  if (effective) query.course_id = effective;
  const data = await api('/student-today', { query });

  const counts = data.counts || {};
  const stat = (label, value) =>
    '<div class="stat"><div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value">' + esc(value) + '</div></div>';

  const classes = data.classes_today || [];
  const study = data.study || [];
  const paths = data.learning_paths || [];
  const pending = data.pending_exercises || [];
  const evaluations = data.recent_evaluations || [];
  const attention = data.attention || [];
  const review = data.review || { items: [], total: 0 };
  const reviewItems = review.items || [];

  // 课程上下文: "All Courses" 时每条都显示课程名, 选了课程就不重复显示。
  // 名称缺失时留空, **不**退回 course_id —— 投影层保证每行都带 course_name
  // (tests/test_student_today.py 有两条契约盯着), 前端不需要那个哈希。
  const courseTag = (row) =>
    data.course_id
      ? ''
      : ' <span class="tiny muted">' + esc(row.course_name || '') + '</span>';

  const kpHref = (courseId, kpId) =>
    '#/courses/' + encodeURIComponent(courseId) + '/knowledge/' + encodeURIComponent(kpId);

  // ---- Today's Classes ------------------------------------------------
  //
  // 全部课程口径下按课程分组 (任务书 §9): 只显示**今天真的有课**的课程,
  // 没课的课程不出空卡片; 单课程筛选时仍是一张平表 (课程名在副标题里)。
  // 分组是**渲染层**对后端已给数据的重组, 不发第二个请求。
  function groupedClassesHtml() {
    if (data.course_id) {
      return classesTable(classes);
    }
    const byCourse = [];
    const index = {};
    for (const row of classes) {
      const key = row.course_id || '';
      if (index[key] === undefined) {
        index[key] = byCourse.length;
        byCourse.push({
          course_id: row.course_id,
          course_name: row.course_name || '',
          sessions: [],
        });
      }
      byCourse[index[key]].sessions.push(row);
    }
    return byCourse.map((group) => (
      '<div class="today-group"><h3>' + esc(group.course_name || '') + '</h3>' +
      classesTable(group.sessions) + '</div>'
    )).join('');
  }

  /** 今日课堂表 (分组内外共用同一张表结构, 无第二份列定义)。 */
  function classesTable(rows) {
    return '<table class="data">' + tableCaption(t('today.classesCard')) + '<thead><tr><th scope="col">' + t('class.session') + '</th><th scope="col">' +
      t('common.status') + '</th><th scope="col" class="num">' + t('course.knowledge') +
      '</th><th scope="col" class="num">' + t('course.pendingReview') +
      '</th></tr></thead><tbody>' +
      rows.map((s) => (
        '<tr><td><a href="#/courses/' + encodeURIComponent(s.course_id) + '/sessions/' +
        encodeURIComponent(s.session_id) + '">' + esc(s.title || t('(无标题)')) + '</a>' +
        '<div class="tiny muted">' +
        (data.course_id ? esc(s.course_name || '') : '') +
        (s.date ? (data.course_id ? ' · ' : '') + esc(s.date) : '') + '</div></td>' +
        '<td>' + pill(sessionStatusLabel(s.status), s.status) + '</td>' +
        '<td class="num">' + esc((s.counts || {}).knowledge_points || 0) + '</td>' +
        '<td class="num">' + esc((s.counts || {}).pending_review || 0) + '</td></tr>'
      )).join('') + '</tbody></table>';
  }

  const classesHtml = classes.length
    ? groupedClassesHtml()
    : emptyState(t('today.noSessions'));

  // ---- Your Study (来自 StudyPlan, 不重新生成) --------------------------
  const studyHtml = study.length
    ? study.map((block) => {
        const tasks = block.tasks_today || [];
        const nextTask = block.next_task || null;
        return '<div class="subblock">' +
          '<div class="subblock-head"><strong>' +
          esc(block.course_name || '') + '</strong>' +
          (block.plan_id
            ? ' <span class="tiny muted mono">' + esc(block.plan_id) + '</span>'
            : '') + '</div>' +
          (block.available
            ? '<p class="small">' + esc(block.tasks_total) + ' ' + t('today.taskCount') +
              (block.rules_version
                ? ' <span class="tiny muted mono">' + esc(block.rules_version) + '</span>'
                : '') + '</p>' +
              (tasks.length
                ? '<ul class="small">' + tasks.slice(0, 10).map((item) => (
                    '<li><a href="' + kpHref(block.course_id, item.knowledge_point_id) + '">' +
                    esc(item.knowledge_point_id) + '</a>' +
                    ((item.reason_codes || []).length
                      ? ' <span class="tiny muted mono">' +
                        esc(item.reason_codes.join(', ')) + '</span>'
                      : '') + '</li>'
                  )).join('') + '</ul>'
                : '') +
              (nextTask
                ? '<p class="small">' + t('today.next') + ': <a href="' +
                  kpHref(block.course_id, nextTask.knowledge_point_id) + '">' +
                  esc(nextTask.knowledge_point_id) + '</a></p>'
                : '') +
              '<p><a class="btn primary" href="#/courses/' +
              encodeURIComponent(block.course_id) + '/students/' +
              encodeURIComponent(block.student_id) + '">' + t('today.continue') + '</a></p>'
            : '<p class="muted small">' + esc(block.note || t('today.noActivity')) + '</p>') +
          '</div>';
      }).join('')
    : '<p class="muted small">' + esc(data.note || t('today.noActivity')) + '</p>';

  // ---- Learning Path (Current / Prerequisite / Next) -------------------
  const pathRow = (row) => {
    const current = row.current || {};
    const kp = current.knowledge_point || {};
    const prereq = row.prerequisites || [];
    const unmet = row.unmet_prerequisites || [];
    return '<li>' +
      '<a href="' + kpHref(row.course_id, row.knowledge_point_id) + '">' +
      esc(kp.title || row.knowledge_point_id) + '</a>' +
      ' ' + pill(stateLabel(current.state), current.state) +
      courseTag(row) +
      '<div class="tiny muted">' + t('path.current') + ': ' +
      esc(current.knowledge_point_id || row.knowledge_point_id) +
      (current.status ? ' · ' + esc(current.status) : '') + '</div>' +
      (prereq.length
        ? '<div class="tiny muted">' + t('path.prerequisite') + ': ' +
          esc(prereq.join(', ')) +
          (unmet.length
            ? ' <span class="badge">' + t('path.blockedBy') + ': ' + esc(unmet.join(', ')) +
              '</span>'
            : '') + '</div>'
        : '') +
      '<div class="tiny muted">' + t('path.nextEvent') + ': ' +
      esc(current.next_event || '—') +
      (current.status_basis
        ? ' · <span class="mono">' + esc(current.status_basis) + '</span>'
        : '') + '</div>' +
      '</li>';
  };
  const pathsHtml = paths.length
    ? '<ul class="small">' + paths.slice(0, 10).map(pathRow).join('') + '</ul>'
    : emptyState(t('path.noPath'));

  // ---- Review ----------------------------------------------------------
  const reviewHtml = reviewItems.length
    ? '<ul class="small">' + reviewItems.slice(0, 10).map((r) => (
        '<li><a href="' + kpHref(r.course_id, r.knowledge_point_id) + '">' +
        esc(r.knowledge_point_id) + '</a> ' +
        pill(r.validation_status, r.validation_status) + courseTag(r) +
        (r.needs_verification
          ? ' <span class="badge">' + t('待验证') + '</span>'
          : '') + '</li>'
      )).join('') + '</ul>'
    : '<p class="muted small">' + t('today.noReview') + '</p>';

  // ---- Pending Exercises ----------------------------------------------
  const exercisesHtml = pending.length
    ? '<ul class="small">' + pending.slice(0, 10).map((e) => (
        '<li><a href="#/courses/' + encodeURIComponent(e.course_id) + '/exercises/' +
        encodeURIComponent(e.exercise_id) + '">' +
        esc(e.prompt || e.exercise_id) + '</a>' +
        ' <span class="tiny muted mono">' + esc(e.exercise_type || '') + '</span>' +
        courseTag(e) + '</li>'
      )).join('') + '</ul>'
    : '<p class="muted small">' + t('today.noExercises') + '</p>';

  // ---- Recent Evaluations (评估就是评估, 不改写成 Mastery) --------------
  const evalHtml = evaluations.length
    ? '<table class="data">' + tableCaption(t('today.evalsCard')) + '<thead><tr><th scope="col">' + t('common.exercise') + '</th><th scope="col">' +
      t('common.answer') + '</th><th scope="col">' + t('ex.status') + '</th><th scope="col" class="num">' +
      t('common.score') + '</th></tr></thead><tbody>' +
      evaluations.slice(0, 10).map((r) => (
        '<tr><td><a href="#/courses/' + encodeURIComponent(r.course_id) + '/exercises/' +
        encodeURIComponent(r.exercise_id) + '">' + esc(r.exercise_id) + '</a>' +
        courseTag(r) + '</td>' +
        '<td class="small break-all">' + dash(r.submitted_value) + '</td>' +
        '<td>' + pill(r.status, r.status) + '</td>' +
        '<td class="num">' + (r.score === null || r.score === undefined
          ? '—' : esc(r.score)) + '</td></tr>'
      )).join('') + '</tbody></table>' +
      '<p class="tiny muted">' + t('ex.notFactVerification') + '</p>'
    : '<p class="muted small">' + t('today.noEvaluations') + '</p>';

  // ---- Attention (只显示 StudentState 已定义的信号) ---------------------
  const attentionHtml = attention.length
    ? '<ul class="small">' + attention.slice(0, 10).map((row) => (
        '<li>' + pill(t('attention.' + row.kind), row.kind) + ' ' +
        '<a href="' + kpHref(row.course_id, row.knowledge_point_id) + '">' +
        esc(row.knowledge_point_id) + '</a>' + courseTag(row) +
        '<div class="tiny muted mono">' + esc(row.basis || '') + '</div></li>'
      )).join('') + '</ul>'
    : '<p class="muted small">' + t('attention.none') + '</p>';

  setView(
    '<div class="page-head"><h1>' + t('today.studentTitle') + '</h1>' +
    '<p class="subtitle mono small">' + esc(data.date || '—') + ' · ' +
    // 全部课程 / 单课程筛选: 与真实请求口径一致 (跟着 data.course_id 走,
    // 而不是猜)。筛选视图给一个回全部的出口。
    (data.course_id
      ? esc(courseLabel(data.course_id)) + ' · <a href="#/today">' +
        esc(t('scope.viewAll')) + '</a>'
      : esc(t('today.allCourses'))) +
    '</p>' +
    // Task 66: 「开始今天的学习」入口。词条 today.startStudy 早在 Task 56
    // 就写好了三语版本 —— 但**从来没有任何渲染代码用过它**, 于是这条
    // 入口在实际界面里不存在。这里把它接上。
    '<p><a class="btn" href="#/learn">' + t('today.startStudy') + '</a></p>' +
    '</div>' +

    '<div class="grid grid-4" style="margin-bottom:16px">' +
    stat(t('today.classesCard'), counts.classes_today || 0) +
    stat(t('today.study'), counts.study_tasks || 0) +
    stat(t('today.reviewCard'), counts.review || 0) +
    stat(t('today.exercisesCard'), counts.pending_exercises || 0) +
    stat(t('today.evalsCard'), counts.recent_evaluations || 0) +
    stat(t('today.attentionCard'), counts.attention || 0) +
    '</div>' +

    (data.has_activity
      ? ''
      : '<div class="card"><p class="muted">' + esc(data.note || t('today.noActivity')) +
        '</p></div>') +

    '<div class="grid grid-2">' +

    // 63.11 - Today's Classes: 全部课程口径下按课程分组渲染。
    '<div class="card"><div class="card-head"><h2>' + t('today.classesCard') + '</h2>' +
    (counts.classes_today
      ? '<span class="small muted">' + esc(counts.classes_today) + '</span>'
      : '') + '</div>' + classesHtml + '</div>' +

    // 63.11 - Review
    '<div class="card"><div class="card-head"><h2>' + t('today.reviewCard') + '</h2>' +
    '<a class="small" href="#/reviews">' + t('today.openReview') + ' →</a></div>' +
    '<p class="small muted">' + esc(review.total || 0) + ' ' + t('today.itemCount') + '</p>' +
    reviewHtml + '</div>' +

    // 63.7 - Exercises
    '<div class="card"><div class="card-head"><h2>' + t('today.exercisesCard') + '</h2>' +
    '<a class="small" href="#/exercises">' + t('today.openExercises') + ' →</a></div>' +
    '<p class="small muted">' + esc(counts.pending_exercises || 0) + ' ' +
    t('today.pendingCount') + '</p>' + exercisesHtml + '</div>' +

    // 63.5 - Learning Path
    '<div class="card"><div class="card-head"><h2>' + t('student.learningPath') +
    '</h2></div>' + pathsHtml + '</div>' +

    // 63.8 - Recent Evaluations
    '<div class="card"><div class="card-head"><h2>' + t('today.evalsCard') + '</h2></div>' +
    evalHtml + '</div>' +

    // 63.9 - Attention Area
    '<div class="card"><div class="card-head"><h2>' + t('today.attentionCard') + '</h2>' +
    '<span class="small muted">' + t('today.attentionNote') + '</span></div>' +
    attentionHtml + '</div>' +

    '</div>' +

    // 63.4 - Today's Study Plan
    '<div class="card"><div class="card-head"><h2>' + t('today.study') + '</h2>' +
    '<span class="small muted">' + t('today.studyFromPlan') + '</span></div>' +
    studyHtml + '</div>'
  );
}

// ---- 我的课程 (Task 68) --------------------------------------------------
//
// 这一页是**全部课程**的总览。它刻意不做三件事:
//
// 1. 不给课程排优先级 —— 顺序是 course_id 的字典序, 一个稳定但无含义的键。
//    "该先学哪门"需要本产品没有的事实 (考试权重 / 个人目标), 编不出来。
// 2. 不把两门课的数字合并后再拆分 —— 每个数字都来自带 course_id 的查询,
//    因此"过滤条件写漏就串课"在结构上不可能发生。
// 3. 不把"证据校验"和"人工审核"合成一个分数 —— 那是两条独立的真相轴。

/** 计数键的固定展示顺序 —— 与后端 COUNT_KEYS 一致。 */
const MC_COUNT_KEYS = [
  'sessions', 'materials', 'knowledge_points', 'pending_review',
  'conflicts', 'students', 'exercises', 'answers',
];

const MC_VALIDATION_KEYS = ['supported', 'unverified', 'conflicted'];
const MC_REVIEW_KEYS = ['confirmed', 'pending', 'rejected', 'kept_unverified'];

/**
 * 两条真相轴的取值标签。
 *
 * 为什么**不用** "前缀 + 变量" 拼 key 的写法
 * --------------------------------------------------
 *
 * 两条轴的取值域不同 (``supported/unverified/conflicted`` vs
 * ``confirmed/pending/rejected/kept_unverified``)。拼前缀时一旦写错前缀,
 * ``t()`` 找不到 key 会**原样返回** ``'val.confirmed'`` —— 页面看起来
 * "有数据", 没有异常、没有报错, 实际上是一整条轴漏译。i18n 键的静态检查
 * 也抓不到它 (源码里根本没有那个字面 key)。
 *
 * 逐个字面写出来之后, 每个 key 都出现在源码里, 键完整性测试能真的对上。
 */
function mcAxisLabels() {
  return {
    supported: t('val.supported'),
    unverified: t('val.unverified'),
    conflicted: t('val.conflicted'),
    confirmed: t('rev.confirmed'),
    pending: t('rev.pending'),
    rejected: t('rev.rejected'),
    kept_unverified: t('rev.kept_unverified'),
  };
}

function mcAxisRow(label, keys, bucket) {
  const labels = mcAxisLabels();
  return '<div class="mc-axis"><span class="tiny muted">' + esc(label) + '</span>' +
    keys.map((key) => (
      '<span class="pill pill-muted">' + esc(labels[key] || key) + ' ' +
      esc(fmtNumber((bucket || {})[key] || 0)) + '</span>'
    )).join(' ') + '</div>';
}

function mcCard(row, selectedId) {
  const isCurrent = row.course_id === selectedId;
  const last = row.last_session;
  return '<div class="card' + (isCurrent ? ' card-current' : '') + '">' +
    '<div class="card-head"><h3>' + esc(row.name || row.code || '') +
    (isCurrent ? ' <span class="pill pill-ok">' + esc(t('mc.current')) + '</span>' : '') +
    '</h3><a class="btn" href="#/courses/' + encodeURIComponent(row.course_id) + '">' +
    esc(t('mc.open')) + '</a></div>' +
    '<p class="tiny muted mono">' + esc(courseIdentity(row)) + '</p>' +
    '<div class="mc-counts">' + MC_COUNT_KEYS.map((key) => (
      '<span class="pill pill-muted">' + esc(t('mc.count.' + key)) + ' ' +
      esc(fmtNumber((row.counts || {})[key] || 0)) + '</span>'
    )).join(' ') + '</div>' +
    mcAxisRow(t('mc.axisValidation'), MC_VALIDATION_KEYS, row.validation) +
    mcAxisRow(t('mc.axisReview'), MC_REVIEW_KEYS, row.review) +
    '<p class="small muted">' + esc(t('mc.lastSession')) + ': ' +
    (last ? esc(last.title || ('#' + last.session_number)) : esc(t('mc.noLastSession'))) +
    ' · ' + esc(t('mc.evidence')) + ' ' + esc(fmtNumber(row.evidence_total || 0)) +
    ' · ' + esc(t('mc.gaps')) + ' ' + esc(fmtNumber(row.gaps || 0)) + '</p>' +
    '</div>';
}
