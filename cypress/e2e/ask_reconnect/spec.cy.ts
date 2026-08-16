import { submitMessage } from '../../support/testUtils';

function dropSocket() {
  // Close the underlying engine.io transport: socket.io reconnects on its
  // own, which is exactly what a network blip looks like to the app.
  cy.window().then((win) => {
    (win as any).__chainlitSocket.io.engine.close();
  });
}

function answerNameAsk() {
  // Wait for the ask step before typing: the composer remounts when the
  // first message arrives and would lose anything typed earlier.
  cy.get('.step').should('contain', 'What is your name?');
  submitMessage('Jeeves');
  cy.get('.step').should('contain', 'Your name is: Jeeves');
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

  it('restores action buttons after a socket drop', () => {
    answerNameAsk();
    cy.get('#continue-action').should('be.visible');

    dropSocket();

    cy.get('#continue-action').should('be.visible').and('not.be.disabled');
    // The re-emitted action must not duplicate the button.
    cy.get('#continue-action').should('have.length', 1);

    cy.get('#continue-action').click();
    cy.get('.step').should('contain', 'Action picked: continue');
  });

  it('restores action buttons after a page reload', () => {
    answerNameAsk();
    cy.get('#continue-action').should('be.visible');

    cy.reload();

    cy.get('#continue-action').should('be.visible').and('not.be.disabled');
    cy.get('#continue-action').click();
    cy.get('.step').should('contain', 'Action picked: continue');
  });

  it('answers exactly once even with rapid double clicks', () => {
    answerNameAsk();

    cy.get('#continue-action').should('be.visible').dblclick();

    cy.get('.step')
      .filter(':contains("Action picked: continue")')
      .should('have.length', 1);
  });

  it('times out from the original deadline despite a reload', () => {
    answerNameAsk();
    cy.get('#continue-action').should('be.visible');

    cy.reload();

    // The ask must come back after the reload…
    cy.get('#continue-action').should('be.visible');
    // …and still expire on the server's original 20s deadline.
    cy.get('.step', { timeout: 30000 }).should(
      'contain',
      'Timed out: no action was taken'
    );
  });
});
