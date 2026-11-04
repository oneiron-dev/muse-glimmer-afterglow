import { useId } from "react";

function Node({ item, x, y, width = 104 }) {
  return <g><rect x={x - width / 2} y={y - 30} width={width} height="60" rx="8" className="ex-node" />
    <foreignObject x={x - width / 2 + 4} y={y - 27} width={width - 8} height="54"><div xmlns="http://www.w3.org/1999/xhtml" className="ex-node-label">{item.label}</div></foreignObject>
  </g>;
}

function Diagram({ body, title }) {
  const marker = `arrow-${useId().replace(/:/g, "")}`;
  const parts = body.type === "parts", cycle = body.type === "cycle";
  const items = parts ? body.parts : body.steps;
  const rows = Math.ceil(items.length / 3);
  const height = parts ? Math.max(160, items.length * 80) : cycle ? 350 : Math.max(110, rows * 112);
  const positions = items.map((item, i) => {
    if (parts) return { item, x: 85, y: 45 + i * 80 };
    if (cycle) {
      const angle = -Math.PI / 2 + i * 2 * Math.PI / items.length;
      return { item, x: 200 + Math.cos(angle) * 140, y: 175 + Math.sin(angle) * 125 };
    }
    const row = Math.floor(i / 3), col = row % 2 ? 2 - i % 3 : i % 3;
    return { item, x: 58 + col * 142, y: 45 + row * 112 };
  });
  return <svg className="ex-diagram" viewBox={`0 0 400 ${height}`} role="img" aria-label={title}>
    <title>{title}</title><desc>{parts ? "Parts of " + body.whole.label + ": " : "In order: "}{items.map((n) => n.label).join(", ")}</desc>
    <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10" fill="currentColor" /></marker></defs>
    {parts ? <>
      <rect x="255" y="15" width="130" height={height - 30} rx="10" className="ex-node" />
      <foreignObject x="266" y={height / 2 - 35} width="108" height="70"><div xmlns="http://www.w3.org/1999/xhtml" className="ex-node-label">{body.whole.label}</div></foreignObject>
      {positions.map((p) => <path key={p.item.id} d={`M143 ${p.y}H255`} className="ex-edge" />)}
    </> : positions.slice(0, -1).map((a, i) => {
      const b = positions[i + 1], dx = b.x - a.x, dy = b.y - a.y;
      const inset = Math.min(52 / (Math.abs(dx) || .001), 30 / (Math.abs(dy) || .001));
      return <path key={a.item.id} d={`M${a.x + dx * inset} ${a.y + dy * inset}L${b.x - dx * inset} ${b.y - dy * inset}`} className="ex-edge" markerEnd={`url(#${marker})`} />;
    })}
    {positions.map((p) => <Node key={p.item.id} {...p} width={parts ? 114 : 104} />)}
  </svg>;
}

function Details({ items }) {
  return <dl className="ex-details">{items.filter((n) => n.detail).map((n) => <div key={n.id}><dt>{n.label}</dt><dd>{n.detail}</dd></div>)}</dl>;
}

export default function Figure({ spec, crop, ocr }) {
  const body = spec.body;
  if (body.type === "text") return <p className="ex-text">{body.text}</p>;
  if (body.type === "compare") return <table className="ex-compare"><thead><tr><th scope="col">Aspect</th><th scope="col">{body.left}</th><th scope="col">{body.right}</th></tr></thead><tbody>{body.rows.map((row) => <tr key={row.id}><th scope="row">{row.aspect}</th><td>{row.left}</td><td>{row.right}</td></tr>)}</tbody></table>;
  if (body.type === "screenshot") {
    const boxes = ocr?.boxes || [];
    return <>
      {crop && <div className="ex-crop"><img src={crop} alt="The area you asked about" />
        {body.pins.map((pin, i) => {
          const b = boxes.find((box) => box.id === pin.anchor)?.box;
          if (!b) return null;
          const left = Math.max(3, Math.min(97, (b[0] + b[2] / 2) * 100));
          const top = Math.max(4, Math.min(96, (b[1] + b[3] / 2) * 100));
          return <span key={pin.item.id} className="ex-pin" style={{ left: `${left}%`, top: `${top}%` }} aria-hidden="true">{i + 1}</span>;
        })}
      </div>}
      <ol className="ex-notes">{body.pins.map((pin) => <li key={pin.item.id}><b>{pin.item.label}</b><p>{pin.item.detail}</p></li>)}</ol>
    </>;
  }
  const items = body.type === "parts" ? [body.whole, ...body.parts] : body.steps;
  return <><Diagram body={body} title={spec.title} /><Details items={items} />
    {body.type === "cycle" && <div className="ex-outcomes" aria-label="Outcomes after the last step">{body.outcomes.map((o) => <section key={o.item.id}><span>{o.when}</span><b>{o.item.label}</b>{o.item.detail && <p>{o.item.detail}</p>}<small>{o.resume === "repeat" ? `Returns to ${body.steps[0].label.toLowerCase()}` : "Stops here"}</small></section>)}</div>}
  </>;
}
