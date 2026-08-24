import { submitMessage } from '../../support/testUtils';

function answerNameAsk(name = 'Jeeves') {
  // Wait for the ask step before typing: the composer remounts when the
  // first message arrives and would lose anything typed earlier.
  cy.get('.step').should('contain', 'What is your name?');
  submitMessage(name);
  cy.get('.step').should('contain', `Your name is: ${name}`);
}

describe('Ask reconnect', () => {
  it('restores a text ask after a page reload', () => {
    cy.get('.step').should('contain', 'What is your name?');

    cy.reload();

    cy.get('.step').should('contain', 'What is your name?');
    // on_chat_start must not have run a second time.
    cy.get('.step')
      .filter(':contains("What is your name?")')
      .should('have.length', 1);

    submitMessage('Jeeves');
    cy.get('.step').should('contain', 'Your name is: Jeeves');
  });

  it('delivers a click made while the transport is down', () => {
    answerNameAsk();
    cy.get('#continue-action').should('be.visible');

    // Keep the transport down until the click has happened: without this,
    // the automatic reconnect could race the click and the test would
    // silently degrade to an ordinary online click. Note: the Manager
    // reads its private _reconnection flag, so the accessor must be used —
    // mutating io.opts has no effect.
    cy.window().then((win) => {
      const socket = (win as any).__chainlitSocket;
      socket.io.reconnection(false);
      socket.io.engine.close();
    });
    cy.window().its('__chainlitSocket.connected').should('eq', false);

    cy.get('#continue-action').click();
    // The reply must actually sit in the offline buffer, proving the click
    // happened while disconnected.
    cy.window()
      .its('__chainlitSocket.sendBuffer')
      .should('have.length.greaterThan', 0);

    cy.window().then((win) => {
      const socket = (win as any).__chainlitSocket;
      socket.io.reconnection(true);
      socket.connect();
    });
    cy.window().its('__chainlitSocket.connected').should('eq', true);
    cy.get('.step').should('contain', 'Action picked: continue');
  });

  it('restores action buttons after a reconnect', () => {
    answerNameAsk();
    cy.get('#continue-action').should('be.visible');

    cy.window().then((win) => {
      (win as any).__chainlitSocket.io.engine.close();
    });
    cy.window().its('__chainlitSocket.connected').should('eq', false);
    // Wait out the automatic reconnect, then interact: the re-emitted ask
    // must rebind the form without duplicating the button.
    cy.window().its('__chainlitSocket.connected').should('eq', true);

    cy.get('#continue-action').should('not.be.disabled').click();
    cy.get('.step').should('contain', 'Action picked: continue');
    // Checked after the round-trip so a late re-emitted duplicate would be
    // caught too.
    cy.get('#continue-action').should('have.length.lte', 1);
  });

  it('restores buttons whose emits were dropped in a dead socket', () => {
    // "dropped" asks the app to wait 5s before sending the action ask —
    // the window in which this test kills the transport, so the action
    // and ask emits land in a dead socket and are silently dropped.
    answerNameAsk('dropped');

    cy.window().then((win) => {
      const socket = (win as any).__chainlitSocket;
      socket.io.reconnection(false);
      socket.io.engine.close();
    });
    cy.window().its('__chainlitSocket.connected').should('eq', false);

    // Stay offline past the app's window. The ask must NOT have arrived —
    // this guards the test against silently degrading into the ordinary
    // online-delivery scenario on a slow CI. The arbitrary wait IS the
    // scenario here: wall-clock must pass while the transport is down so
    // the server's emits land in a dead socket, and no client-observable
    // signal can exist for that (the socket is dead).
    // eslint-disable-next-line cypress/no-unnecessary-waiting
    cy.wait(6000);
    cy.get('.step').should('not.contain', 'Pick an action!');

    cy.window().then((win) => {
      const socket = (win as any).__chainlitSocket;
      socket.io.reconnection(true);
      socket.connect();
    });
    cy.window().its('__chainlitSocket.connected').should('eq', true);

    // The restore must bring back BOTH halves of the form: the ask text
    // (re-emitted ask payload) and the buttons (unconditional
    // restore_actions re-emit — before the fix these stayed gated behind
    // "the client has UI state" and the form came back buttonless).
    cy.get('.step').should('contain', 'Pick an action!');
    cy.get('#continue-action').should('be.visible').and('not.be.disabled');
    cy.get('#continue-action').click();
    cy.get('.step').should('contain', 'Action picked: continue');
  });

  it('restores the transcript and action buttons after a page reload', () => {
    answerNameAsk();
    cy.get('#continue-action').should('be.visible');

    cy.reload();

    // The transcript is replayed from the server, not just the form.
    cy.get('.step').should('contain', 'Your name is: Jeeves');
    cy.get('#continue-action').should('be.visible').and('not.be.disabled');
    cy.get('#continue-action').click();
    cy.get('.step').should('contain', 'Action picked: continue');
  });

  it('starts a fresh chat on reload when nothing is pending', () => {
    answerNameAsk();
    cy.get('#continue-action').click();
    cy.get('.step').should('contain', 'Action picked: continue');

    cy.reload();

    // No pending ask: F5 keeps its historical meaning — a brand-new chat.
    cy.get('.step').should('contain', 'What is your name?');
    cy.get('.step')
      .filter(':contains("Action picked")')
      .should('have.length', 0);
  });

  it('answers exactly once even with rapid double clicks', () => {
    answerNameAsk();

    cy.get('#continue-action').should('be.visible').dblclick();

    cy.get('.step')
      .filter(':contains("Action picked: continue")')
      .should('have.length', 1);
  });

  it('times out from the original deadline despite a reload', () => {
    // "timeout" asks the app for a short (20s) action deadline.
    answerNameAsk('timeout');
    cy.get('#continue-action').should('be.visible');

    cy.reload();

    // The ask must come back after the reload…
    cy.get('#continue-action').should('be.visible');
    // …and still expire on the server's original deadline.
    cy.get('.step', { timeout: 30000 }).should(
      'contain',
      'Timed out: no action was taken'
    );
  });
});
