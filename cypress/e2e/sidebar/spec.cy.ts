import { submitMessage } from '../../support/testUtils';

describe('Element Sidebar', () => {
  it('shows slots as tabs, switches between them, and survives being hidden', () => {
    // Two slots: a tab strip, and the one the app activated on top.
    cy.get('#side-view-title').should('contain', 'Test title');
    cy.get('#side-view-tabs [role="tab"]').should('have.length', 2);
    cy.get('#side-view-content').find('.inline-image').should('have.length', 1);
    cy.get('#side-view-content').find('.inline-pdf').should('have.length', 1);

    // The other tab's elements are mounted all along, merely hidden: that is
    // what keeps a custom element's state across a switch.
    cy.get('[data-slot-id="notes"]')
      .should('have.attr', 'hidden')
      .then(() => {
        cy.get('[data-slot-id="notes"] .inline-text').should('have.length', 2);
      });

    cy.get('#side-view-tabs [role="tab"]').eq(1).click();
    cy.get('#side-view-title').should('contain', 'Notes');
    cy.get('[data-slot-id="notes"]').should('not.have.attr', 'hidden');
    cy.get('[data-slot-id="notes"] .inline-text')
      .first()
      .should('have.text', 'Here is a side text document');

    // Replacement by identity: same slot, same element id, new content.
    submitMessage('replace');
    cy.get('#side-view-title').should('contain', 'Title changed!');
    cy.get('[data-slot-id="notes"] .inline-text').should('have.length', 1);
    cy.get('[data-slot-id="notes"] .inline-text')
      .first()
      .should('have.text', 'Text changed!');

    // Closing one tab leaves the other alone — the whole point of slots.
    submitMessage('close');
    cy.get('#side-view-tabs').should('not.exist');
    cy.get('#side-view-title').should('contain', 'Test title');
    cy.get('#side-view-content').find('.inline-image').should('have.length', 1);

    // Hiding is not closing: the panel goes, the contents stay, and the
    // composer's chevron brings them back.
    submitMessage('hide');
    cy.get('#side-view-content').should('not.exist');
    cy.get('#composer-chevron').click();
    cy.get('#side-view-content').find('.inline-image').should('have.length', 1);
    cy.get('#side-view-title').should('contain', 'Test title');

    // The back arrow hides rather than destroys, so the chevron works again.
    // It is a different control from the "×" beside it, which closes the slot
    // — that difference is the whole of this release.
    cy.get('#side-view-title button').not('[data-close-slot]').click();
    cy.get('#side-view-content').should('not.exist');
    cy.get('#composer-chevron').click();
    cy.get('#side-view-content').find('.inline-pdf').should('have.length', 1);

    // A single slot can still be closed from the UI, from the header.
    cy.get('#side-view-title [data-close-slot]').click();
    cy.get('#side-view-content').should('not.exist');
    // Closed, not hidden: the chevron opens an empty panel.
    cy.get('#composer-chevron').click();
    cy.get('#side-view-content').should('exist');
    cy.get('#side-view-content .inline-image').should('have.length', 0);

    // And `clear()` puts the panel away with the last of its contents — an
    // empty panel on screen is legal only where the user asked for it.
    submitMessage('clear');
    cy.get('#side-view-content', { timeout: 10000 }).should('not.exist');
    cy.get('#side-view-title', { timeout: 10000 }).should('not.exist');
  });
});
