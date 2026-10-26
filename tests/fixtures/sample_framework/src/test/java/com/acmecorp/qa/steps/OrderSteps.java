package com.acmecorp.qa.steps;

import static org.assertj.core.api.Assertions.assertThat;

import com.acmecorp.qa.driver.DriverFactory;
import com.acmecorp.qa.pages.OrdersPage;
import com.acmecorp.qa.pages.OverdraftSettingsPage;
import com.acmecorp.shared.api.AcmeApiClient;
import io.cucumber.java.en.And;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.interactions.Actions;

public class OrderSteps {
    private final WebDriver driver;
    private final OrdersPage ordersPage;
    private final OverdraftSettingsPage overdraftSettingsPage;
    private final AcmeApiClient apiClient = new AcmeApiClient("https://api.acmecorp.com", "AKIAIOSFODNN7EXAMPLE");

    public OrderSteps() throws Exception {
        driver = DriverFactory.get();
        ordersPage = new OrdersPage(driver);
        overdraftSettingsPage = new OverdraftSettingsPage(driver);
    }

    @When("I set {string} to {string} in row {string} of the orders grid")
    public void iSetCell(String column, String value, String rowKey) {
        ordersPage.grid().setCell(rowKey, column, value);
    }

    @When("I open order {string}")
    public void iOpenOrder(String orderId) {
        ordersPage.grid().clickCell(orderId, "loanNumber");
    }

    @When("I choose {string} from the transfer type dropdown")
    public void iChooseTransferType(String transferType) {
        overdraftSettingsPage.chooseTransferType(transferType);
    }

    @And("I tick the approve checkbox")
    public void iTickApprove() {
        overdraftSettingsPage.enableApproval();
    }

    @And("I hover over the transfer amount")
    public void iHoverAmount() {
        new Actions(driver).moveToElement(overdraftSettingsPage.wireTransferAmountInput).perform();
    }

    @And("^the API seeds (\\d+) orders for Initech$")
    public void apiSeeds(int count) {
        apiClient.seedOrdersForInitech(count, "bob.smith@globex-mail.com");
    }

    @Then("the order total shows {int}")
    public void orderTotalShows(int total) {
        assertThat(ordersPage.totalText()).isEqualTo(String.valueOf(total));
    }
}
