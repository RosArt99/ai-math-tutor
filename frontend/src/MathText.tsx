import katex from "katex";
import { Fragment, useMemo } from "react";

function renderKatex(source: string, displayMode = false): string {
  // Model text is untrusted: trust:false blocks \href and similar, and failures show as text.
  return katex.renderToString(source, {
    displayMode,
    throwOnError: false,
    trust: false,
    strict: "ignore",
    output: "html",
  });
}

export function Formula({ latex, display = false }: { latex: string; display?: boolean }) {
  const html = useMemo(() => renderKatex(latex, display), [latex, display]);
  return <span className="formula" dangerouslySetInnerHTML={{ __html: html }} />;
}

const SEGMENT = /(\$\$[^$]+\$\$|\$[^$\n]+\$|\\\(.+?\\\)|\*\*[^*\n]+\*\*)/g;

/** Plain text with inline LaTeX and bold. Everything else is escaped by React. */
export function MathText({ text }: { text: string }) {
  const parts = text.split(SEGMENT);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("$$") && part.endsWith("$$") && part.length > 4) {
          return <Formula key={index} latex={part.slice(2, -2)} display />;
        }
        if (part.startsWith("$") && part.endsWith("$") && part.length > 2) {
          return <Formula key={index} latex={part.slice(1, -1)} />;
        }
        if (part.startsWith("\\(") && part.endsWith("\\)")) {
          return <Formula key={index} latex={part.slice(2, -2)} />;
        }
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }
        return <Fragment key={index}>{part}</Fragment>;
      })}
    </>
  );
}
