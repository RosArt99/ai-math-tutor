import type { Problem, Progress, SkillReport, TopicReport } from "./api";
import { lookup, t } from "./i18n";
import { Formula } from "./MathText";
import { SyntaxGuide } from "./Syntax";

function Score({ skill }: { skill: SkillReport }) {
  const grade = skill.grade;
  const label = grade === null ? t.notStarted : `${grade} out of 5`;
  return (
    <span className="score" aria-label={label} title={lookup(t.skillStatus, skill.status)}>
      <span className="score-bars" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((level) => (
          <span
            key={level}
            className={grade !== null && level <= grade ? `bar bar-on bar-${grade}` : "bar"}
          />
        ))}
      </span>
      <span className="score-value">{grade ?? "–"}</span>
    </span>
  );
}

function SkillList({ topic }: { topic: TopicReport }) {
  return (
    <ul className="skill-list">
      {topic.skills.map((skill) => (
        <li key={skill.skill} className="skill-row">
          <span className={`skill-name ${skill.status === "needs_practice" ? "skill-weak" : ""}`}>
            {skill.title}
          </span>
          <Score skill={skill} />
        </li>
      ))}
    </ul>
  );
}

function Stats({ progress }: { progress: Progress }) {
  return (
    <div className="stats">
      <div>
        <strong>{progress.completed}</strong>
        <span>{t.solved}</span>
      </div>
      <div>
        <strong>{progress.clean}</strong>
        <span>{t.cleanSolved}</span>
      </div>
      <div>
        <strong>{progress.mistakes_total}</strong>
        <span>{t.mistakesFound}</span>
      </div>
      <div>
        <strong>
          {progress.follow_ups_passed}/{progress.follow_ups_total}
        </strong>
        <span>{t.followUpsPassed}</span>
      </div>
    </div>
  );
}

function RecentMistakes({ progress }: { progress: Progress }) {
  return (
    <>
      <h3 className="small-caps">{t.recentMistakes}</h3>
      {progress.recent_mistakes.length === 0 ? (
        <p className="fine-print">{t.noMistakes}</p>
      ) : (
        <ul className="mistakes">
          {progress.recent_mistakes.map((mistake, index) => (
            <li key={index}>
              <span>{lookup(t.mistakes, mistake.type)}</span>
              {mistake.equation && <code>{mistake.equation}</code>}
              <span className={mistake.resolved ? "tag-fixed" : "tag-open"}>
                {mistake.resolved ? t.fixed : t.open}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

interface BookProps {
  progress: Progress;
  problem: Problem;
  onOpenTopic: (id: string) => void;
}

/** The home screen: an open textbook. Title page on the left, contents on the right. */
export function Book({ progress, problem, onOpenTopic }: BookProps) {
  return (
    <div className="spread">
      <section className="leaf leaf-left" aria-label="Title page">
        <p className="small-caps">{t.bookKicker}</p>
        <h2 className="book-title">{t.bookTitle}</h2>
        <p className="book-lead">{t.bookLead}</p>

        <ol className="how">
          {t.howItWorks.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ol>

        <h3 className="small-caps">{t.syntaxTitle}</h3>
        <SyntaxGuide />

        {progress.completed + progress.mistakes_total > 0 && (
          <>
            <h3 className="small-caps">{t.yourRecord}</h3>
            <Stats progress={progress} />
          </>
        )}
        <span className="folio">i</span>
      </section>

      <section className="leaf leaf-right" aria-label={t.contents}>
        <h2 className="contents-heading">{t.contents}</h2>
        <p className="fine-print">{t.contentsHint}</p>
        {progress.topics.map((topic) => {
          const current = topic.id === problem.topic;
          return (
            <article key={topic.id} className={`toc-entry ${current ? "toc-current" : ""}`}>
              {/* Students recognise a kind of problem by its shape more than by its name. */}
              <button
                type="button"
                className="toc-open"
                onClick={() => onOpenTopic(topic.id)}
                aria-label={`${current ? t.continueChapter : t.openChapter}: ${topic.title}`}
              >
                <span className="toc-number">{topic.number}</span>
                <span className="toc-text">
                  <span className="toc-title">{topic.title}</span>
                  <span className="toc-summary">{topic.summary}</span>
                </span>
                <span className="toc-example">
                  <Formula latex={topic.example_latex} />
                </span>
                <span className="toc-action">{current ? t.continueChapter : t.openChapter} →</span>
              </button>
              <SkillList topic={topic} />
            </article>
          );
        })}
        <p className="fine-print">{t.gradeHint}</p>
        <span className="folio">1</span>
      </section>
    </div>
  );
}

/** The margin beside the lesson: the open chapter's skills and the student's record. */
export function ChapterMargin({ progress, problem }: { progress: Progress; problem: Problem }) {
  const topic = progress.topics.find((item) => item.id === problem.topic) ?? progress.topics[0];
  return (
    <aside className="margin" aria-label={topic.title}>
      <p className="small-caps">
        {t.chapter} {topic.number}
      </p>
      <h2 className="margin-title">{topic.title}</h2>
      <div className="margin-example">
        <Formula latex={topic.example_latex} />
      </div>
      <SkillList topic={topic} />
      <p className="fine-print">{t.gradeHint}</p>
      <Stats progress={progress} />
      <RecentMistakes progress={progress} />
    </aside>
  );
}
