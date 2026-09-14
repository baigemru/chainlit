const resizeObserverLoopErrRe = /^[^(ResizeObserver loop limit exceeded)]/;
Cypress.on('uncaught:exception', (err) => {
  /* returning false here prevents Cypress from failing the test */
  if (resizeObserverLoopErrRe.test(err.message)) {
    return false;
  }
});

export function submitMessage(message: string) {
  cy.get('#chat-input')
    .should('be.visible')
    .should('not.be.disabled')
    .type(message);
  cy.get('#chat-submit').should('not.be.disabled').click();
}

export function openHistory() {
  cy.get(`#chat-input`).should('not.be.disabled').type(`{upArrow}`);
}

export function closeHistory() {
  cy.get(`body`).click();
}

/**
 * Read frames off the live socket, by tag.
 *
 * The wire is plain JSON discriminated on `t` -- there is no Engine.IO `42`
 * prefix to strip and no event-name array to unpack. Call it before
 * `cy.visit`, since it stubs the constructor on `window:before:load`.
 *
 * The only thing it is for is reaching values the UI never shows: the
 * server-minted `session.ready.sessionId`, an element's `chainlitKey`.
 * Assert on the DOM for anything the user can see.
 */
export function onServerFrames(handlers: Record<string, (frame: any) => void>) {
  cy.on('window:before:load', (win) => {
    const OriginalWebSocket = win.WebSocket;

    // One stub for every tag the caller wants. Sinon refuses to wrap a method
    // twice ("already wrapped"), so a per-tag stub would throw the moment a
    // spec watched two frames.
    cy.stub(win, 'WebSocket').callsFake(
      (url: string, protocols?: string | string[]) => {
        const ws = new OriginalWebSocket(url, protocols);

        ws.addEventListener('message', (event: MessageEvent) => {
          const data = event.data;
          if (typeof data !== 'string') return;
          try {
            const frame = JSON.parse(data);
            handlers[frame?.t]?.(frame);
          } catch (_e) {
            // Ignore parse errors
          }
        });

        return ws;
      }
    );
  });
}
