// 本番サイトの「過去レースの検証データ収集」を操作する運用補助（取得は本番アプリの共通ゲートを通る）。
// 使い方: BT_WORK=名前 BT_DATES=YYYYMMDD,... [BT_SURFACE=ダート|地方 BT_VENUE=浦和,大井] node scripts/ops/backtest_prod.js start|status|zip <保存先>
const { chromium } = require('playwright');
const action = process.argv[2] || 'status';
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1100, height: 1400 }, acceptDownloads: true });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  await p.goto('https://keiba-analysis-lab2026.streamlit.app/', { waitUntil: 'domcontentloaded', timeout: 90000 }); await p.waitForTimeout(10000);
  const wake = p.locator('button', { hasText: 'back up' });
  if (await wake.count()) { await wake.first().click(); console.log('woke'); await p.waitForTimeout(120000); await p.reload(); await p.waitForTimeout(20000); }
  const F = () => p.frames().find(x => x.url().includes('/~/+/')) || p.mainFrame();
  for (let i = 0; i < 30; i++) { const t = await F().locator('body').innerText().catch(() => ''); if (/競馬分析ラボ/.test(t)) break; await p.waitForTimeout(10000); }
  await F().getByText('🧰 管理・検証').first().click(); await p.waitForTimeout(8000);
  await F().locator('[data-testid="stExpander"] summary', { hasText: '過去レースの検証データ収集' }).first().click(); await p.waitForTimeout(3000);
  const box = F().locator('[data-testid="stExpander"]', { hasText: '過去レースの検証データ収集' });
  const area = box.getByLabel('対象日（YYYYMMDD、カンマ区切り）'); await area.fill(process.env.BT_DATES || ''); await area.press('Tab'); await p.waitForTimeout(3000);
  if (process.env.BT_WORK) { const nm = box.getByLabel('作業名（英数字）'); await nm.fill(process.env.BT_WORK); await nm.press('Tab'); await p.waitForTimeout(3000); }
  if (process.env.BT_SURFACE) { await box.locator('[data-testid="stRadio"] label', { hasText: process.env.BT_SURFACE }).first().click(); await p.waitForTimeout(3000); }
  if (process.env.BT_VENUE) { const v = box.getByLabel('競馬場（カンマ区切り）'); await v.fill(process.env.BT_VENUE); await v.press('Tab'); await p.waitForTimeout(3000); }
  if (action === 'start') { const btn = box.getByRole('button', { name: /収集を開始/ }); console.log('disabled', await btn.isDisabled()); if (!(await btn.isDisabled())) { await btn.click(); await p.waitForTimeout(8000); } }
  if (action === 'zip') { const dir = process.argv[3]; await box.getByRole('button', { name: 'ZIPを作成' }).click(); await p.waitForTimeout(20000);
    const dl = box.locator('[data-testid="stDownloadButton"] button', { hasText: '.zip' }); const name = (await dl.first().innerText()).trim();
    const [d] = await Promise.all([p.waitForEvent('download'), dl.first().click()]); await d.saveAs(dir + '/' + name); console.log('saved', name); }
  await box.getByRole('button', { name: '状況を更新' }).click().catch(() => {}); await p.waitForTimeout(3000);
  const t = await box.innerText(); console.log((t.match(/(状態：[^\n]*|結果：[^\n]*)/g) || []).join('\n'));
  await b.close();
})();
