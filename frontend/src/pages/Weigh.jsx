import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Weigh() {
  const [sites, setSites] = useState([])
  const [scales, setScales] = useState([])
  const [rows, setRows] = useState([])
  const [cards, setCards] = useState([])
  const [siteId, setSiteId] = useState('')
  const [scaleId, setScaleId] = useState('')
  const [gross, setGross] = useState('1200')
  const [tare, setTare] = useState('800')
  const [deduct, setDeduct] = useState('0')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = async () => {
    const [s, sc, open, waiting] = await Promise.all([
      api('/回收点'),
      api('/地磅'),
      api('/过磅'),
      api('/席位牌?状态=' + encodeURIComponent('候席')),
    ])
    setSites(s)
    setScales(sc)
    setRows(open)
    setCards(waiting)
    const openSites = s.filter((x) => x.状态 === '营业中')
    const sid = siteId || (openSites[0] && String(openSites[0].编号)) || ''
    if (!siteId && sid) setSiteId(sid)
    const usable = sc.filter((x) => String(x.回收点编号) === String(sid) && x.状态 !== '故障')
    if (!usable.some((x) => String(x.编号) === String(scaleId))) {
      const free = usable.find((x) => x.状态 === '空闲')
      setScaleId(String((free || usable[0] || {}).编号 || ''))
    }
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const siteScales = scales.filter((x) => String(x.回收点编号) === String(siteId) && x.状态 !== '故障')
  const siteCards = cards.filter((x) => String(x.回收点编号) === String(siteId))

  const submit = async () => {
    setErr(''); setMsg('')
    try {
      const row = await api('/过磅', {
        method: 'POST',
        body: JSON.stringify({
          回收点编号: Number(siteId),
          地磅编号: Number(scaleId),
          毛重: Number(gross),
          皮重: Number(tare),
          扣杂: Number(deduct),
        }),
      })
      if (row.正式落条 === false) {
        setMsg(`同秤已有正式磅条，后到请求已领席位牌 ${row.席位牌号}（${row.席位状态}）；不会生成第二张正式磅条。`)
      } else {
        setMsg(`入站成功，磅条 ${row.磅条号}，席位牌 ${row.席位牌号}，地磅已钉住`)
      }
      setScaleId('')
      await load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>过磅</h2>
      <div className="row">
        <label>回收点
          <select value={siteId} onChange={(e) => { setSiteId(e.target.value); setScaleId('') }}>
            {sites.filter((s) => s.状态 === '营业中').map((s) => (
              <option key={s.编号} value={s.编号}>{s.名称}</option>
            ))}
          </select>
        </label>
        <label>地磅席位闸
          <select value={scaleId} onChange={(e) => setScaleId(e.target.value)}>
            <option value="">请选择地磅</option>
            {siteScales.map((s) => (
              <option key={s.编号} value={s.编号}>
                {s.地磅} · {s.状态}{s.钉住磅条号 ? ` · 钉住 ${s.钉住磅条号}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>毛重<input value={gross} onChange={(e) => setGross(e.target.value)} /></label>
        <label>皮重<input value={tare} onChange={(e) => setTare(e.target.value)} /></label>
        <label>扣杂<input value={deduct} onChange={(e) => setDeduct(e.target.value)} /></label>
        <button type="button" onClick={submit} disabled={!scaleId}>过磅 / 撞车领牌</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <h3>当前未结正式磅条</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>磅条号</th><th>回收点</th><th>地磅</th><th>状态</th><th>毛重</th><th>皮重</th><th>扣杂</th><th>净重</th><th>席位牌</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.磅条号}</td><td>{r.回收点}</td><td>{r.地磅}</td><td>{r.状态}</td>
                <td>{r.毛重}</td><td>{r.皮重}</td><td>{r.扣杂}</td><td>{r.净重}</td>
                <td>{r.席位牌号 || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>本点候席席位牌</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>席位牌号</th><th>地磅</th><th>队列</th><th>状态</th><th>超时时间</th></tr></thead>
          <tbody>
            {siteCards.map((c) => (
              <tr key={c.编号}>
                <td>{c.席位牌号}</td><td>{c.地磅}</td><td>{c.队列序号}</td>
                <td>{c.状态}</td><td>{c.超时时间}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
