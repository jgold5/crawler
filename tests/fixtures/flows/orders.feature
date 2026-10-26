@orders
Feature: Order editing

  Background:
    Given the user is logged in
    And the API seeds 3 orders
      | id    |
      | A-100 |

  @smoke @flow:open_orders
  Scenario: Open orders
    When I go to the "Orders" page
    And I am on the "OrdersPage" page
    Then I should see "Orders"

  Scenario: Fill the profile form
    When I open "Settings"
    And I fill the form:
      | field   | value       |
      | Email   | ${USER}     |
      | Display | Bob Builder |
    And I click the 2nd "Save" in "Security"
    And I check "Active"
    And I select "Admin" from "Role"
    And I press "Enter" in "Search"
    Then I should be on the "Settings" page

  Scenario: Grid edits
    When the user opens orders screen
    And I set "Quantity" to "5" in row "A-100" of the "Orders" grid
    And I set column 3 to "7" in the row where "Order ID" is "A-100" of the "Orders" grid
    And I click "Edit" in row "A-101"
    And I select "Shipped" for "Status" in row "A-100" of the "Orders" grid
    And I check "Paid" in row "A-102" of the "Orders" grid
    And the user updates "Quantity" to "9" for order "A-103"
    And the user enters "hello" in the "Notes" box
    And the user saves and confirms the dialog

  @outline
  Scenario Outline: Edit <id>
    When I set "Quantity" to "<qty>" in row "<id>" of the "Orders" grid
    Examples:
      | id    | qty |
      | A-100 | 5   |
      | A-200 | 6   |
