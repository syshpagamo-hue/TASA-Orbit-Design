import { getStore, getDeployStore } from '@netlify/blobs';

const json = (data: unknown, status = 200) => Response.json(data, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
const safeText = (v: unknown, max = 80) => String(v ?? '').trim().slice(0, max);

export default async (request: Request, context: any) => {
  const url = new URL(request.url);
  const production = context.deploy.context === 'production';
  const store = production
    ? getStore('sy-maker-order-v1', { consistency: 'strong' })
    : getDeployStore('sy-maker-preview-v1', { consistency: 'strong' });

  if (request.method !== 'GET' && request.headers.get('origin') && request.headers.get('origin') !== url.origin) {
    return json({ error: '不允許跨來源送出' }, 403);
  }

  if (url.pathname === '/api/config' && request.method === 'GET') {
    return json({ ok: true, mode: production ? 'production' : 'preview' });
  }

  if (url.pathname === '/api/leaderboard' && request.method === 'GET') {
    const { blobs } = await store.list({ prefix: 'scores/' });
    const rows: any[] = [];
    for (const b of blobs) {
      const row = await store.get(b.key, { type: 'json' });
      if (row) rows.push(row);
    }
    rows.sort((a, b) => b.score - a.score || a.name.localeCompare(b.name, 'zh-Hant'));
    return json({ items: rows.slice(0, 20) });
  }

  if (url.pathname === '/api/leaderboard' && request.method === 'POST') {
    const body: any = await request.json();
    const name = safeText(body.name, 24);
    const score = Number(body.score);
    if (!name || !Number.isInteger(score) || score < 0 || score > 100000) return json({ error: '排行榜資料格式不正確' }, 400);
    const key = `scores/${encodeURIComponent(name.toLocaleLowerCase('zh-TW'))}`;
    const prior: any = await store.get(key, { type: 'json' });
    if (!prior || score > prior.score) await store.setJSON(key, { name, score, updatedAt: new Date().toISOString() });
    return json({ ok: true }, 201);
  }

  if (url.pathname === '/api/orders' && request.method === 'POST') {
    const body: any = await request.json();
    const order = body.order ?? {};
    const quote = body.quote ?? {};
    const name = safeText(order?.customer?.name, 50);
    if (!name) return json({ error: '請填寫姓名' }, 400);
    if (!['member', 'school', 'other'].includes(order.role)) return json({ error: '身分資料不正確' }, 400);
    if (!Array.isArray(quote.rows) || !Number.isFinite(Number(quote.total)) || Number(quote.total) <= 0) return json({ error: '訂購內容不正確' }, 400);
    const id = `SY-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}-${crypto.randomUUID().replaceAll('-', '').slice(0, 12).toUpperCase()}`;
    const receipt = {
      id,
      createdAt: new Date().toISOString(),
      role: order.role,
      customer: {
        name,
        className: safeText(order?.customer?.className, 20),
        seat: safeText(order?.customer?.seat, 5),
        phone: safeText(order?.customer?.phone, 30)
      },
      note: safeText(order.note, 1500),
      question: safeText(order.question, 1500),
      quote
    };
    await store.setJSON(`orders/${id}`, receipt);
    return json({ receipt }, 201);
  }

  return json({ error: '找不到此服務' }, 404);
};

export const config = {
  path: '/api/*',
  rateLimit: { windowLimit: 120, windowSize: 60, aggregateBy: ['ip', 'domain'] }
};