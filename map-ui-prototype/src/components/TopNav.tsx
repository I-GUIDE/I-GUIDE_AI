interface Props {
  onToggleSettings: () => void;
  onToggleHistory: () => void;
  sessionCount: number;
}

// The header for the rs-embed deployment (issue #20). This used to mirror the I-GUIDE platform
// chrome with non-functional placeholders — Collections / Apps / Support / a search box — which
// made the page look like the platform without behaving like it: every one of them was dead on
// click. They are gone, and the one link that DOES go somewhere replaces them.
//
// History / gear / jpy / account are kept: the first two are wired, and the last two still carry
// the platform look. The account avatar remains a placeholder.
export function TopNav(p: Props) {
  return (
    <header className="bar">
      <div className="bar-inner">
        <div className="brand">
          <svg className="iglogo" width="26" height="26" viewBox="0 0 100 100" aria-hidden="true">
            <polygon points="50,6 89,28 89,72 50,94 11,72 11,28" fill="none" stroke="#1aa37a" strokeWidth="9" strokeLinejoin="round" />
            <polygon points="50,26 71,38 71,62 50,74 29,62 29,38" fill="#a8b400" opacity="0.85" />
          </svg>
          <span className="brand-name">Remote Sensing (rs-embed)</span>
        </div>
        <nav className="top">
          <a className="platform-link" href="https://platform.i-guide.io"
             target="_blank" rel="noopener noreferrer">
            &larr; I-GUIDE Platform
          </a>
        </nav>
        <div className="grow" />
        <button className="navbtn" title="Past conversations" onClick={p.onToggleHistory}>
          History{p.sessionCount ? ` (${p.sessionCount})` : ''}
        </button>
        <button className="navbtn gear" title="Connection settings" onClick={p.onToggleSettings}>⚙</button>
        <span className="jpy" title="Jupyter (placeholder)">jpy</span>
        <span className="avatar" title="Account (placeholder)" />
      </div>
    </header>
  );
}
