import { useId } from "react";

// Every shape is laid out here from the spec's words; the model never writes markup or coordinates.
// Pins sit on host-known places in the picture (anchors: [{id, text, box: [x, y, w, h] fractions}]).

function Node({ item, x, y, width = 104 }) {
  return <g><rect x={x - width / 2} y={y - 30} width={width} height="60" rx="8" className="ex-node" />
    <foreignObject x={x - width / 2 + 4} y={y - 27} width={width - 8} height="54"><div xmlns="http://www.w3.org/1999/xhtml" className="ex-node-label">{item.label}</div></foreignObject>
  </g>;
}

// Steps left to right, three to a row, the next row running back (a snake), or round a circle for a cycle.
function Steps({ steps, cycle, title }) {
  const marker = `arrow-${useId().replace(/:/g, "")}`;
  const rows = Math.ceil(steps.length / 3);
  const height = cycle ? 300 : Math.max(110, rows * 112);
  const positions = steps.map((item, i) => {
    if (cycle) {
      const angle = -Math.PI / 2 + i * 2 * Math.PI / steps.length;
      return { item, x: 200 + Math.cos(angle) * 130, y: 150 + Math.sin(angle) * 100 };
    }
    const row = Math.floor(i / 3), col = row % 2 ? 2 - i % 3 : i % 3;
    return { item, x: 58 + col * 142, y: 45 + row * 112 };
  });
  const links = positions.slice(0, cycle ? positions.length : -1).map((a, i) => [a, positions[(i + 1) % positions.length]]);
  return <svg className="ex-diagram" viewBox={`0 0 400 ${height}`} role="img" aria-label={title}>
    <title>{title}</title><desc>{"In order: " + steps.map((n) => n.label).join(", ")}</desc>
    <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10" fill="currentColor" /></marker></defs>
    {links.map(([a, b]) => {
      const dx = b.x - a.x, dy = b.y - a.y;
      const inset = Math.min(52 / (Math.abs(dx) || .001), 30 / (Math.abs(dy) || .001));
      return <path key={a.item.id} d={`M${a.x + dx * inset} ${a.y + dy * inset}L${b.x - dx * inset} ${b.y - dy * inset}`} className="ex-edge" markerEnd={`url(#${marker})`} />;
    })}
    {positions.map((p) => <Node key={p.item.id} {...p} />)}
  </svg>;
}

function Notes({ items }) {
  return <ol className="ex-notes">{items.map((item) => <li key={item.id}><b>{item.label}</b>{item.detail && <p>{item.detail}</p>}</li>)}</ol>;
}

function Details({ items }) {
  const shown = items.filter((n) => n.detail);
  return shown.length ? <dl className="ex-details">{shown.map((n) => <div key={n.id}><dt>{n.label}</dt><dd>{n.detail}</dd></div>)}</dl> : null;
}

const pct = (v) => `${Math.max(0, Math.min(100, v * 100)).toFixed(2)}%`;

function Picture({ crop, pins = [], anchors = [] }) {
  const placed = pins.map((pin) => ({ pin, box: anchors.find((a) => a.id === pin.anchor)?.box })).filter((p) => p.box);
  return <div className="ex-crop"><img src={crop} alt="The area you asked about" />
    {placed.map(({ pin, box }, i) => <span key={pin.id} className="ex-mark" aria-hidden="true"
      style={{ left: pct(box[0]), top: pct(box[1]), width: pct(box[2]), height: pct(box[3]) }}>
      <span className="ex-pin">{pins.indexOf(pin) + 1}</span>
    </span>)}
  </div>;
}

export default function Figure({ spec, crop, anchors }) {
  const body = spec?.body || { type: "text", text: "" };
  if (body.type === "screenshot") return <>{crop && <Picture crop={crop} pins={body.pins} anchors={anchors} />}<Notes items={body.pins} /></>;
  if (body.type === "list") return <Notes items={body.items} />;
  if (body.type === "flow") return <><Steps steps={body.steps} title={spec.title} /><Details items={body.steps} /></>;
  if (body.type === "cycle") return <><Steps steps={body.steps} cycle title={spec.title} /><Details items={body.steps} />
    <div className="ex-outcomes" aria-label="What happens after the last step">{body.outcomes.map((o) => <section key={o.id}><span>{o.when}</span><b>{o.label}</b>{o.detail && <p>{o.detail}</p>}<small>{o.resume === "repeat" ? `Back to ${body.steps[0].label.toLowerCase()}` : "Stops here"}</small></section>)}</div>
  </>;
  if (body.type === "compare") return <table className="ex-compare"><thead><tr><th scope="col"><span className="sr">Point</span></th><th scope="col">{body.left}</th><th scope="col">{body.right}</th></tr></thead><tbody>{body.rows.map((row) => <tr key={row.id}><th scope="row">{row.aspect}</th><td>{row.left}</td><td>{row.right}</td></tr>)}</tbody></table>;
  return <>{crop && <Picture crop={crop} />}{body.text && <p className="ex-text">{body.text}</p>}</>;
}
