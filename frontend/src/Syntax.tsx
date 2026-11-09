import { Formula } from "./MathText";

/** How to type mathematics on a keyboard; many students have never needed to. */
const ROWS: [string, string][] = [
  ["x^2", "x^2"],
  ["3x", "3x  or  3*x"],
  ["\\frac{x}{2}", "x/2"],
  ["\\frac{x + 1}{3}", "(x + 1)/3"],
  ["3(x + 4)", "3(x + 4)"],
  ["(x + 3)^2", "(x + 3)^2"],
  ["x = 7", "x = 7"],
];

export function SyntaxGuide({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <ul className="syntax-chips">
        {ROWS.map(([latex, typed]) => (
          <li key={typed}>
            <Formula latex={latex} />
            <span aria-hidden="true">→</span>
            <code>{typed}</code>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className={`syntax ${compact ? "syntax-compact" : ""}`}>
      <table>
        <thead>
          <tr>
            <th scope="col">You mean</th>
            <th scope="col">Type</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([latex, typed]) => (
            <tr key={typed}>
              <td>
                <Formula latex={latex} />
              </td>
              <td>
                <code>{typed}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="syntax-note">
        Write one step per line, or chain forms with <code>=</code>. Words are fine too.
      </p>
    </div>
  );
}

/** Turn typed input into LaTeX for a live preview, so students see how it will be read. */
export function previewLatex(text: string): string | null {
  const trimmed = text.trim();
  // Only preview mathematics, not sentences: no words longer than one letter.
  if (!trimmed || /[a-wyzа-яё]{2,}/i.test(trimmed) || !/[x\d]/i.test(trimmed)) return null;
  return trimmed
    .split("\n")
    .map((line) =>
      line
        .replace(/\*\*/g, "^")
        .replace(/\*/g, " \\cdot ")
        .replace(/\^(\d+)/g, "^{$1}")
        .replace(/х/g, "x"),
    )
    .join(" \\\\ ");
}
