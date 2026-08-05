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

    // on_chat_start ran first, then the first message was sent as user input
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'searching knife');
    cy.get('.step').should('contain', 'profile: Search');

    // A round trip settles the socket, so a late re-add or a switch loop
    // would show up in the final count
    submitMessage('ping');
    cy.get('.step').should('have.length', 5);
    cy.get('.step').should('not.contain', 'hello');
    cy.get('#chat-profiles').should('contain.text', 'Search');
  });

  it('should answer AskUserMessage with the first message', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('go ask');

    cy.get('#chat-profiles').should('contain.text', 'AskSearch');
    cy.get('.step').should('contain', 'What are you looking for?');
    cy.get('.step').should('contain', 'ask answered: knife please');

    submitMessage('ping');
    cy.get('.step').should('have.length', 5);
  });

  it('should not answer a non-text ask with the first message', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('go action');

    cy.get('#chat-profiles').should('contain.text', 'ActionSearch');
    cy.get('.step').should('contain', 'Pick a search mode');

    // Replying to an action ask with a text step would raise server-side
    cy.get('.step').should('not.contain', 'Error');
    cy.get('.step').should('not.contain', 'knife via action');

    // Once the action is answered, on_chat_start ends and delivery resumes
    cy.get('#first-action').should('exist').click();
    cy.get('.step').should('contain', 'action answered: by_photo');
    cy.get('.step').should('contain', 'knife via action');
    cy.get('.step').should('contain', 'profile: ActionSearch');
  });

  it('should switch cleanly when the config refetch is slow', () => {
    let requests = 0;
    cy.intercept('GET', '**/project/settings*', (req) => {
      requests += 1;
      // Only delay the refetch that follows the profile change, so that it
      // outlasts the 200ms connect debounce.
      if (requests > 1) {
        req.on('response', (res) => res.setDelay(600));
      }
    });

    cy.get('#chat-input').should('exist');
    submitMessage('go search');

    cy.get('#chat-profiles').should('contain.text', 'Search');
    // on_chat_start of the new profile must not be skipped
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'searching knife');
    cy.get('.step').should('contain', 'profile: Search');

    submitMessage('ping');
    cy.get('.step').should('have.length', 5);
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
