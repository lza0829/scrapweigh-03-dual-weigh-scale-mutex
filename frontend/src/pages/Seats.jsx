import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Seats() {
  const [cards, setCards] = useState([])
  const [segments, setSegments] = useState([])
  const [gate, setGate] = useState([])
  const [replay, setReplay] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = async () => {
    setErr('')
    const [c, s, g] = await Promise.all([
      api('/席位牌册'),
      api('/态势占用段'),
      api('/地磅席位闸'),
    ])
    setCards(c)
    setSegments(s)
    setGate(g)
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  const act = async (path, options) => {
    setErr(''); setMsg('')
    try {
      const data = await api(path, options)
      if (path.includes('重放')) setReplay(data)
      else if (data.磅条号) setMsg(`已补落正式磅条 ${data.磅条号}`)
      else setMsg('操作已同刷')
      await load()
    } catch (e) { setErr(e.message) }
  }

  const expireAll = () => act('/席位牌/超时作废', { method: 'POST', body: '{}' })

  return (
    <div className="panel">
      <h2>席位牌册与态势占用段</h2>
      <div className="row">
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
        <button type="button" onClick={expireAll}>超时作废扫描</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <h3>地磅席位闸 / 一秤一未结正式</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>回收点</th><th>地磅</th><th>钉住灯</th><th>钉住票</th><th>未结正式数</th><th>候席牌</th><th>闭合</th></tr></thead>
          <tbody>
            {gate.map((g) => (
              <tr key={g.地磅编号}>
                <td>{g.回收点}</td><td>{g.地磅}</td><td>{g.钉住灯状态}</td>
                <td>{g.钉住磅条号 || '-'}</td><td>{g.未结正式磅条数}</td><td>{g.候席牌数}</td>
                <td>{g.一秤一未结正式 ? '是' : '否'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>席位牌册</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>牌号</th><th>核验码</th><th>地磅</th><th>队列</th><th>状态</th><th>正式磅条</th><th>超时时间</th><th>操作</th></tr></thead>
          <tbody>
            {cards.map((c) => (
              <tr key={c.编号}>
                <td>{c.席位牌号}</td><td>{c.核验码}</td><td>{c.地磅}</td><td>{c.队列序号}</td>
                <td>{c.状态}</td><td>{c.磅条号 || '-'}</td><td>{c.超时时间}</td>
                <td className="row">
                  {c.状态 === '候席' && (
                    <button type="button" onClick={() => act(`/席位牌/${c.编号}/补落`, { method: 'POST' })}>补落</button>
                  )}
                  <button type="button" className="secondary" onClick={() => act(`/席位牌/${c.编号}/重放`)}>重放</button>
                  {c.状态 === '候席' && (
                    <button type="button" className="secondary" onClick={() => act(`/席位牌/${c.编号}/超时作废`, { method: 'POST' })}>超时作废</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>回收态势占用段</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>占用段号</th><th>回收点</th><th>地磅</th><th>磅条</th><th>席位牌</th><th>状态</th><th>开始</th><th>结束</th></tr></thead>
          <tbody>
            {segments.map((s) => (
              <tr key={s.编号}>
                <td>{s.占用段号}</td><td>{s.回收点}</td><td>{s.地磅}</td>
                <td>{s.磅条号 || '-'}</td><td>{s.席位牌号 || '-'}</td>
                <td>{s.状态}</td><td>{s.开始时间}</td><td>{s.结束时间 || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {replay && (
        <>
          <h3>席位结局重放：{replay.重放结局}（{replay.结局一致 ? '与册页一致' : '不一致'}）</h3>
          <pre className="json-box">{JSON.stringify(replay.事件流, null, 2)}</pre>
        </>
      )}
    </div>
  )
}
