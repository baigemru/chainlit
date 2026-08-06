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

  it('should keep the transcript and mark where the new chat starts', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('hello');
    cy.get('.step').should('have.length', 2);

    submitMessage('go soft');

    cy.get('#chat-profiles').should('contain.text', 'Search');

    // The previous conversation stays on screen, above a single divider
    cy.get('.chat-boundary').should('have.length', 1);
    cy.get('.chat-boundary').should('contain.text', 'Search');
    cy.get('.chat-boundary').prevAll('.step').should('contain', 'hello');

    // The new chat runs below it
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'searching knife');

    submitMessage('ping');
    // 3 kept (hello, its answer, the trigger) + 3 from the new chat + 2 for ping
    cy.get('.step').should('have.length', 8);

    // The trigger is not shown on both sides of the divider
    cy.get('.step:contains("go soft")').should('have.length', 1);
    // ...and the retained half cannot be edited into the new session
    cy.get('.chat-boundary')
      .prevAll('.step')
      .find('.edit-message')
      .should('not.exist');
  });

  it('should not show the trigger on both sides of the divider', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('hello');
    submitMessage('go echo');

    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('have.length', 4);

    // The trigger was redelivered to the new chat, so it is only shown there
    cy.get('.step:contains("go echo")').should('have.length', 1);
    cy.get('.chat-boundary')
      .prevAll('.step')
      .should('not.contain', 'go echo')
      .and('contain', 'hello');

    // The app re-matches its own trigger on the redelivered message, so this
    // also proves the loop guard: no second switch, no second divider.
    cy.get('.chat-boundary').should('have.length', 1);
  });

  it('should start a new chat in the same profile when keeping the transcript', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('hello');
    cy.get('.step').should('have.length', 2);

    submitMessage('go soft same');

    cy.get('.chat-boundary').should('have.length', 1);
    cy.get('#chat-profiles').should('contain.text', 'Assistant');
    cy.get('.step').should('contain', 'hello');

    submitMessage('ping');
    cy.get('.chat-boundary')
      .nextAll('.step')
      .should('contain', 'profile: Assistant');
  });

  it('should drop the retained transcript on reload', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('hello');
    submitMessage('go soft');
    cy.get('.chat-boundary').should('have.length', 1);

    cy.reload();

    cy.get('#chat-input').should('exist');
    cy.get('.chat-boundary').should('not.exist');
    cy.get('.step').should('not.exist');
  });
});
