import { submitMessage } from '../../support/testUtils';

describe('Wait message', () => {
  it('shimmers, rotates the texts and ends wait mode on update()', () => {
    submitMessage('start');

    // The loader arrives in wait mode with the first rotation text.
    cy.get('[data-test="wait-message"]').should('exist');
    cy.get('[data-test="wait-message"]').should('contain.text', 'текст 1');

    // Client-side rotation: every 2s the displayed text advances.
    cy.get('[data-test="wait-message"]', { timeout: 6000 }).should(
      'contain.text',
      'текст 2'
    );
    // loop=false: the rotation reaches the last text and holds on it.
    cy.get('[data-test="wait-message"]', { timeout: 6000 }).should(
      'contain.text',
      'текст 3'
    );

    // update() ends wait mode: the attribute is gone, the result is shown.
    cy.get('[data-test="wait-message"]', { timeout: 15000 }).should(
      'not.exist'
    );
    cy.get('.step').should('contain.text', 'Готово: финальный ответ.');
    cy.get('.step').should('not.contain.text', 'текст 1');
  });

  it('a newer message deactivates wait mode on the previous one', () => {
    submitMessage('next');

    cy.get('[data-test="wait-message"]').should('exist');

    // The follow-up message lands after the loader and deactivates it.
    cy.get('.step').should('contain.text', 'follow-up message');
    cy.get('[data-test="wait-message"]').should('not.exist');

    // The loader falls back to its persistent content (the first text),
    // never keeping a rotated text on screen.
    cy.get('.step:contains("текст 1")').should('have.length', 1);
    cy.get('.step').should('not.contain.text', 'текст 2');
  });
});
