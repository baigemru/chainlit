import { submitMessage } from '../../support/testUtils';

describe('Programmatic chat profile switch', () => {
  it('should switch profile, start a new chat and hand over the transit message', () => {
    cy.get('#chat-input').should('exist');
    cy.get('#chat-profiles').should('contain.text', 'Assistant');

    submitMessage('hello');
    cy.get('.step').should('have.length', 2);
    cy.get('.step').eq(1).should('contain', 'profile: Assistant');

    submitMessage('go search');

    // The UI switches to the Search profile and opens a fresh chat
    cy.get('#chat-profiles').should('contain.text', 'Search');

    // on_chat_start of the new profile read the transit message
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'transit: searching knife');

    // The transit value is not impersonated as a user reply
    cy.get('.step[data-step-type="user_message"]').should('not.exist');

    // A round trip settles the socket, so a late re-add or a switch loop
    // would show up in the final count
    submitMessage('ping');
    cy.get('.step').should('have.length', 4);
    cy.get('.step').should('not.contain', 'hello');
    cy.get('#chat-profiles').should('contain.text', 'Search');
  });

  it('should not inherit a transit message when none was sent', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('go empty');

    cy.get('#chat-profiles').should('contain.text', 'Search');
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('not.contain', 'transit:');

    submitMessage('ping');
    cy.get('.step').should('have.length', 3);
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
    cy.get('.step').should('contain', 'transit: searching knife');

    submitMessage('ping');
    cy.get('.step').should('have.length', 4);
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

    // The previous conversation stays on screen, above a single divider —
    // including the trigger, which belongs to the chat that ended
    cy.get('.chat-boundary').should('have.length', 1);
    cy.get('.chat-boundary').should('contain.text', 'Search');
    cy.get('.chat-boundary')
      .prevAll('.step')
      .should('contain', 'hello')
      .and('contain', 'go soft');

    // The new chat runs below it
    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'transit: searching knife');

    submitMessage('ping');
    // 3 kept (hello, its answer, the trigger) + 2 from the new chat + 2 for ping
    cy.get('.step').should('have.length', 7);

    // The trigger exists exactly once, above the divider
    cy.get('.step:contains("go soft")').should('have.length', 1);
    // ...and the retained half cannot be edited into the new session
    cy.get('.chat-boundary')
      .prevAll('.step')
      .find('.edit-message')
      .should('not.exist');
  });

  it('should not loop when the transit value equals the trigger', () => {
    cy.get('#chat-input').should('exist');

    submitMessage('hello');
    submitMessage('go echo');

    cy.get('.step').should('contain', 'search ready');
    cy.get('.step').should('contain', 'transit: go echo');
    cy.get('.step').should('have.length', 5);

    // The trigger is a user message exactly once (above the divider); the
    // echo below is the assistant's, so nothing re-enters on_message
    cy.get('.step[data-step-type="user_message"]:contains("go echo")').should(
      'have.length',
      1
    );
    cy.get('.chat-boundary').should('have.length', 1);

    // A round trip proves no delayed second switch either
    submitMessage('ping');
    cy.get('.step').should('have.length', 7);
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
