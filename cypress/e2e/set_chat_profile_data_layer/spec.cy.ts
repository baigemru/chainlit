import { submitMessage } from '../../support/testUtils';

const login = () => {
  cy.location('pathname').should('eq', '/login');
  cy.get('#email').should('be.visible').type('user1');
  cy.get('#password').should('be.visible').type('user1{enter}');
};

describe('Programmatic chat profile switch with a data layer', () => {
  it('should not resume the previous thread when switching from a thread page', () => {
    login();

    cy.get('#chat-input').should('exist');
    cy.get('#chat-profiles').should('contain.text', 'Assistant');

    submitMessage('hello');
    cy.get('.step').should('contain', 'profile: Assistant');

    // The sidebar navigates to the thread page after the first interaction
    cy.location('pathname').should('match', /^\/thread\//);

    submitMessage('go search');

    cy.get('#chat-profiles').should('contain.text', 'Search');
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'searching knife');

    // A round trip settles the socket, so a late resume would land before
    // the invariants below are checked
    submitMessage('ping');
    cy.get('.step').should('have.length', 5);

    // The old thread must not be replayed into the new chat
    cy.get('.step').should('not.contain', 'RESUMED');
    cy.get('.step').should('not.contain', 'hello');
    cy.get('.step').should('not.contain', 'profile: Assistant');

    // The profile must not revert to the previous one
    cy.get('#chat-profiles').should('contain.text', 'Search');
  });

  it('should keep the transcript on screen but split the threads in history', () => {
    login();

    cy.get('#chat-input').should('exist');
    submitMessage('hello');
    cy.get('.step').should('contain', 'profile: Assistant');
    cy.location('pathname').should('match', /^\/thread\//);

    cy.location('pathname').then((oldThreadPath) => {
      submitMessage('go soft');

      cy.get('#chat-profiles').should('contain.text', 'Search');
      // The previous conversation stays on screen...
      cy.get('.step').should('contain', 'hello');
      cy.get('.step').should('contain', 'profile: Assistant');
      // ...while a new chat runs underneath it
      cy.get('.step').should('contain', 'search ready');
      cy.get('.step').should('contain', 'searching knife');
      // The old thread must not have been replayed
      cy.get('.step').should('not.contain', 'RESUMED');

      // The new messages landed in a different thread
      cy.location('pathname')
        .should('match', /^\/thread\//)
        .and('not.eq', oldThreadPath);

      // Both chats are in the history, each opening on its own profile
      cy.get('#thread-history').should('contain.text', 'hello');
      cy.get('#thread-history').should('contain.text', 'searching knife');

      cy.get('#thread-history').contains('hello').click();
      cy.get('.step').should('contain', 'RESUMED');
      cy.get('.step').should('contain', 'hello');
      cy.get('.step').should('not.contain', 'searching knife');
      cy.get('#chat-profiles').should('contain.text', 'Assistant');

      cy.get('#thread-history').contains('searching knife').click();
      cy.get('.step').should('contain', 'searching knife');
      cy.get('.step').should('not.contain', 'hello');
      cy.get('#chat-profiles').should('contain.text', 'Search');
    });
  });
});
