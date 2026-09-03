import { IGuideMark } from './IGuideMark';
import { TopNavPlatform } from './TopNav.platform';
import { isPlatformVariant } from '../uiVariant';

export interface TopNavProps {
  onToggleSettings: () => void;
  onToggleHistory: () => void;
  sessionCount: number;
}

// The header for the rs-embed deployment (issue #20). This used to mirror the I-GUIDE platform
// chrome with non-functional placeholders — Collections / Apps / Support / a search box — which
// made the page look like the platform without behaving like it: every one of them was dead on
// click. They are gone, and the one link that DOES go somewhere replaces them.
//
// The MARK is the link back to the platform, so there is no separate text link. Only History
// and the settings gear remain on the right: the jpy badge and the account avatar were platform
// placeholders that did nothing here.
function TopNavRsEmbed(p: TopNavProps) {
  return (
    <header className="bar">
      <div className="bar-inner">
        <div className="brand">
          <a className="marklink" href="https://platform.i-guide.io" target="_blank"
             rel="noopener noreferrer" aria-label="Back to the I-GUIDE Platform"
             title="Back to the I-GUIDE Platform">
            <IGuideMark className="iglogo" />
          </a>
          <span className="brand-name">I-GUIDE AI</span>
        </div>
        <div className="grow" />
        <button className="navbtn" title="Past conversations" onClick={p.onToggleHistory}>
          History{p.sessionCount ? ` (${p.sessionCount})` : ''}
        </button>
        <button className="navbtn gear" title="Connection settings" onClick={p.onToggleSettings}>⚙</button>
      </div>
    </header>
  );
}

// The original prototype page is kept verbatim in TopNav.platform.tsx and selected here, so
// switching back is a build flag rather than a revert. See src/uiVariant.ts.
export function TopNav(p: TopNavProps) {
  return isPlatformVariant ? <TopNavPlatform {...p} /> : <TopNavRsEmbed {...p} />;
}
