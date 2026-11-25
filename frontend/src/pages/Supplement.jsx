import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Supplement() {
  const [rows, setRows] = useState([])
  const [events, setEvents] = useState([])
  const [ticketId, setTicketId] = useState('')
  const [kind, setKind] = useState('皮重')
  const [weight, setWeight] = useState('')
  const [deduct, setDeduct] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = async () => {
    const [t, e] = await Promise.all([
      api('/未结正式磅条'),
      api('/毛皮事件流水'),
    ])
    setRows(t)
    setEvents(e.slice(0, 100))
    if (!ticketId && t[0]) setTicketId(String(t[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    setErr(''); setMsg('')
    try {
      const body = { 类型: kind }
      if (weight !== '') body.重量 = Number(weight)
      if (deduct !== '') body.扣杂 = Number(deduct)
      const row = await api(`/补称/${ticketId}`, { method: 'POST', body: JSON.stringify(body) })
      setMsg(`补称已落闸：${row.磅条号}，扣杂后货重/净重 ${row.扣杂后货重 ?? row.净重}`)
      setWeight('')
      await load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>补称与毛皮事件流水</h2>
      <div className="row">
        <label>未结磅条
          <select value={ticketId} onChange={(e) => setTicketId(e.target.value)}>
            {rows.map((r) => <option key={r.编号} value={r.编号}>{r.磅条号} · {r.地磅} · 当前净重 {r.净重}</option>)}
          </select>
        </label>
        <label>补称类型
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="皮重">皮重</option>
            <option value="毛重">毛重</option>
          </select>
        </label>
        <label>重量<input value={weight} onChange={(e) => setWeight(e.target.value)} /></label>
        <label>扣杂（可选）<input value={deduct} onChange={(e) => setDeduct(e.target.value)} /></label>
        <button type="button" onClick={submit} disabled={!ticketId || weight === ''}>过补称闸</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <h3>毛皮事件流水</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>事件号</th><th>磅条号</th><th>地磅</th><th>事件</th><th>类别</th><th>重量</th><th>串号</th><th>来源</th><th>时间</th></tr></thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.编号}>
                <td>{e.事件号}</td><td>{e.磅条号 || '-'}</td><td>{e.地磅}</td>
                <td>{e.事件类型}</td><td>{e.重量类别}</td><td>{e.重量}</td>
                <td>{e.串号 || '-'}</td><td>{e.来源}</td><td>{e.时间}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
