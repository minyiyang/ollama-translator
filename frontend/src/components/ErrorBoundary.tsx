import { Component, type ReactNode } from "react";
import { t } from "../i18n";
import { describeError, type ErrorDescription } from "../lib/errors";

type Props = { children: ReactNode; onReload?: () => void };
type State = { failure: ErrorDescription | null };

/**
 * Last-resort catch for errors thrown while drawing a page, which would
 * otherwise unmount the whole app and leave a blank screen. It sits outside the
 * router and every provider, so its fallback must not use their contexts.
 * React already logs the caught error to the console.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { failure: null };

  static getDerivedStateFromError(error: unknown): State {
    return { failure: describeError(error) };
  }

  render() {
    const { failure } = this.state;
    if (!failure) return this.props.children;
    return (
      <main className="page">
        <div className="banner bad" role="alert">
          <b>{t("ui.error.title")}</b>
          <div>{failure.message}</div>
          <p>{t("ui.error.help")}</p>
          <div className="row">
            <button type="button" className="primary" onClick={this.props.onReload ?? (() => window.location.reload())}>
              {t("ui.error.reload")}
            </button>
          </div>
          {failure.details && (
            <details>
              <summary>{t("ui.error.details")}</summary>
              <pre>{failure.details}</pre>
            </details>
          )}
        </div>
      </main>
    );
  }
}
