import { submitMessage } from '../../support/testUtils';

describe('Programmatic chat profile switch', () => {
  it('should switch profile, start a new chat and deliver the first message', () => {
    cy.get('#chat-input').should('exist');
    cy.get('#chat-profiles').should('contain.text', 'Assistant');

    submitMessage('hello');
    cy.get('.step').should('have.length', 2);
    cy.get('.step').eq(1).should('contain', 'profile: Assistant');

    submitMessage('go search');

    // The UI switches to the Search profile and opens a fresh chat
    cy.get('#chat-profiles').should('contain.text', 'Search');
    cy.get('.step').should('not.contain', 'hello');

    // on_chat_start ran first, then the first message was sent as user input
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'searching knife');
    cy.get('.step').should('contain', 'profile: Search');
  });

  it('should answer AskUserMessage with the first message', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('go ask');

    cy.get('#chat-profiles').should('contain.text', 'AskSearch');
    cy.get('.step').should('contain', 'What are you looking for?');
    cy.get('.step').should('contain', 'ask answered: knife please');
  });

  it('should ignore an unknown profile', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('go unknown');
    // A follow-up round-trip guarantees the event has been processed
    submitMessage('hello');

    cy.get('.step').should('have.length', 3);
    cy.get('#chat-profiles').should('contain.text', 'Assistant');
    cy.get('.step').eq(0).should('contain', 'go unknown');
    cy.get('.step').eq(2).should('contain', 'profile: Assistant');
  });

  it('should only move the selector when start_new is false', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('hello');
    cy.get('.step').should('have.length', 2);

    submitMessage('go selector');

    cy.get('#chat-profiles').should('contain.text', 'Search');
    // The current chat is kept
    cy.get('.step').should('contain', 'hello');
    cy.get('.step').should('contain', 'profile: Assistant');
  });
});
