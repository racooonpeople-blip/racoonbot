const languages = [
  "Russian",
  "Hebrew",
  "Arabic",
  "Persian (Farsi)",
  "English",
  "Spanish",
  "French",
  "German",
  "Turkish",
  "Ukrainian",
  "Italian",
  "Portuguese",
  "Chinese",
  "Japanese",
  "Korean",
  "Hindi",
];

const useCases = [
  {
    icon: "◉",
    title: "Turn a voice message into text",
    text: "Get readable text from a voice message or audio file.",
  },
  {
    icon: "⌁",
    title: "Make notes from a lecture",
    text: "Turn lectures and classes into clear study notes.",
  },
  {
    icon: "▣",
    title: "Get text from a video",
    text: "Extract useful text from YouTube or a video file.",
  },
  {
    icon: "◎",
    title: "Translate a recording",
    text: "Turn foreign-language audio into text you understand.",
  },
  {
    icon: "↗",
    title: "Save links and find them later",
    text: "Keep useful material together and retrieve it when you need it.",
  },
  {
    icon: "≡",
    title: "Clean up an interview transcript",
    text: "Remove filler and turn spoken language into readable text.",
  },
];

const features = [
  ["▤", "Turn audio into text", "Convert voice messages, audio and video into accurate text."],
  ["✦", "Make it easier to read", "Clean up spoken language and structure it clearly."],
  ["◎", "Translate into another language", "Understand material across languages."],
  ["≡", "Get notes and key points", "Create summaries, key points and useful notes."],
];

const steps = [
  ["1", "Send", "Share a voice message, audio, video, document or link."],
  ["2", "Process", "Racooon transcribes, cleans and structures it."],
  ["3", "Ask", "Ask questions, request notes or pull out key information."],
  ["4", "Find", "Your material stays searchable for later."],
];

export default function Home() {
  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Racooon home">
          <span className="brandMark">◉</span>
          <span>Racooon</span>
        </a>

        <nav className="nav">
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#use-cases">Use cases</a>
          <a href="#pricing">Pricing</a>
        </nav>

        <div className="headerActions">
          <a className="login" href="#">Log in</a>
          <a className="button black compact" href="#try">Get started</a>
          <details className="languageMenu">
            <summary>◎ Interface language</summary>
            <div className="languageDropdown">
              <button>English</button>
              <button>Русский</button>
              <button>עברית</button>
              <button>العربية</button>
              <button>فارسی</button>
            </div>
          </details>
        </div>
      </header>

      <section className="hero shell" id="top">
        <div className="heroCopy">
          <h1>
            Send voice, video or a link.
            <br />
            Get text, notes or translation.
          </h1>
          <p className="heroLead">
            Turn voice messages, interviews, lectures and videos into text you can
            read, search and use.
          </p>

          <div className="dropzone" id="try">
            <div className="uploadIcon">⇧</div>
            <strong>Drop audio, video or a document here</strong>
            <span>or click to browse files</span>
            <input
              className="fileInput"
              aria-label="Choose an audio, video or document"
              type="file"
              multiple
            />

            <div className="or"><span>or</span></div>

            <form className="linkForm">
              <span className="linkSymbol">↗</span>
              <input
                aria-label="Paste a YouTube or public file link"
                placeholder="Paste a YouTube or public file link..."
              />
              <button type="button" aria-label="Continue">→</button>
            </form>

            <div className="fileTypes" aria-label="Supported input types">
              <span>◉ Voice message</span>
              <span>⌁ Audio</span>
              <span>▣ Video</span>
              <span>▤ Document</span>
              <span>▶ YouTube link</span>
              <span>↗ Public file link</span>
            </div>
          </div>

          <div className="heroButtons">
            <a className="button black" href="#try">Start for free →</a>
            <a className="button light" href="#">✈ Try on Telegram</a>
          </div>
          <small>No credit card required.</small>
        </div>

        <div className="mascotStage" aria-label="Racooon mascot placeholder">
          <div className="sendAnything">Send anything ···</div>
          <div className="floatingTools">
            <span>⌁</span><span>▣</span><span>▤</span><span>↗</span>
          </div>
          <div className="raccoonPlaceholder">
            <div className="raccoonFace">◉</div>
            <div className="hoodie">R</div>
            <div className="laptopShape"></div>
            <div className="tailShape">
              <i></i><i></i><i></i><i></i><i></i>
            </div>
          </div>
          <p className="scribble">…get text,<br />notes or translation<br />in seconds.</p>
        </div>
      </section>

      <section className="languages shell">
        <div className="eyebrow">SUPPORTED LANGUAGES</div>
        <div className="languageChips">
          {languages.map((language) => (
            <span key={language}>{language}</span>
          ))}
        </div>
      </section>

      <section className="section shell" id="use-cases">
        <h2>What do you need help with?</h2>
        <div className="useCaseScroller">
          {useCases.map((item) => (
            <article className="useCaseCard" key={item.title}>
              <div className="cardIcon">{item.icon}</div>
              <h3>{item.title}</h3>
              <p>{item.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section shell" id="features">
        <div className="eyebrow pill">WHAT RACOOON DOES</div>
        <h2>Turn your content into something useful.</h2>
        <div className="featureGrid">
          {features.map(([icon, title, text]) => (
            <article className="feature" key={title}>
              <div className="cardIcon">{icon}</div>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section shell" id="how">
        <div className="eyebrow pill">HOW IT WORKS</div>
        <h2>From a messy file to something you can use.</h2>
        <div className="steps">
          {steps.map(([number, title, text], index) => (
            <article className="step" key={title}>
              <div className="stepTop">
                <span className="stepNumber">{number}</span>
                <div>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              </div>
              <div className="miniUi">
                {index === 0 && <><b>▶</b><span className="wave">|||||||||||||</span><small>0:24</small></>}
                {index === 1 && <><b>▤</b><span className="lines">━━━<br/>━━━━━<br/>━━</span><b>✓</b></>}
                {index === 2 && <><b>◉</b><span>What are the main takeaways?</span></>}
                {index === 3 && <><b>⌕</b><span><strong>Product launch meeting</strong><br/><small>12 key points</small></span></>}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="closing shell" id="pricing">
        <span className="handNote left">Less manual work.<br />More time for what matters.</span>
        <div>
          <h2>Tell Racooon what you need.</h2>
          <p>Send it once. Get text, notes or translation.</p>
          <div className="heroButtons">
            <a className="button black" href="#try">Start for free →</a>
            <a className="button light" href="#">✈ Open in Telegram</a>
          </div>
          <small>No credit card required.</small>
        </div>
        <span className="handNote right">Your audio, video and files<br />ready when you need them.</span>
      </section>
    </main>
  );
}
