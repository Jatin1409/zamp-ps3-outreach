import { ExternalLink, GraduationCap, Github, Globe, Linkedin, Mail } from "lucide-react";
import { ABOUT, AboutLink, initials } from "../about";

const ICON = { linkedin: Linkedin, github: Github, email: Mail, web: Globe };

function LinkChip({ l }: { l: AboutLink }) {
  const I = ICON[l.kind || "web"];
  return <a className="btn secondary sm" href={l.url} target="_blank" rel="noreferrer"><I size={14} /> {l.label} <ExternalLink size={12} /></a>;
}

export default function About() {
  return (
    <div className="page about">
      <section className="about-hero card">
        <div className="about-avatar" aria-hidden={!ABOUT.photoUrl}>
          {ABOUT.photoUrl ? <img src={ABOUT.photoUrl} alt={ABOUT.name} /> : <span>{initials(ABOUT.name)}</span>}
        </div>
        <div className="about-id">
          <div className="eyebrow">About me</div>
          <h1>{ABOUT.name}</h1>
          <p className="muted">{ABOUT.headline}</p>
          <div className="row" style={{ marginTop: 4 }}>
            <span className="pill outline"><GraduationCap size={13} /> {ABOUT.institution}</span>
          </div>
          {ABOUT.links.length > 0 && <div className="row" style={{ marginTop: 12 }}>{ABOUT.links.map((l) => <LinkChip key={l.url} l={l} />)}</div>}
        </div>
      </section>

      {ABOUT.sections.map((s) => (
        <section key={s.title} className="card">
          <h2 className="section-title">{s.title}</h2>
          <dl className="about-list">
            {s.items.map((it) => (
              <div key={it.label} className="about-row">
                <dt>{it.label}</dt>
                <dd>{it.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}

      <section className="card about-note">
        <h2 className="section-title">About this project</h2>
        <p className="muted">Outreach Desk is my submission for Zamp's AI Solutions Analyst case study (PS-3:
          personalised outreach, from prospect to draft). It researches a B2B prospect from public sources, picks the
          most relevant hook, writes a short email built on it and checks it isn't generic — and a person
          approves it before anything goes anywhere. It's an independent build, not a Zamp product.</p>
      </section>
    </div>
  );
}
