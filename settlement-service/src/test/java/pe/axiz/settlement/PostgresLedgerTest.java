package pe.axiz.settlement;

import static org.assertj.core.api.Assertions.*;

import java.math.BigDecimal;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.postgresql.PostgreSQLContainer;
import pe.axiz.settlement.adapters.out.postgresql.JdbcLedger;
import pe.axiz.settlement.domain.SettlementEvent;
import tools.jackson.databind.json.JsonMapper;

@EnabledIfEnvironmentVariable(named = "RUN_POSTGRES_IT", matches = "true")
class PostgresLedgerTest {
  private static PostgreSQLContainer container;
  private JdbcTemplate jdbc;
  private JdbcLedger ledger;

  @BeforeAll
  static void start() {
    container = new PostgreSQLContainer("postgres:18.6");
    container.start();
    Flyway.configure()
        .dataSource(container.getJdbcUrl(), container.getUsername(), container.getPassword())
        .locations("classpath:db/migration")
        .load()
        .migrate();
  }

  @AfterAll
  static void stop() {
    if (container != null) container.stop();
  }

  @BeforeEach
  void init() {
    var ds = new PGSimpleDataSource();
    ds.setURL(container.getJdbcUrl());
    ds.setUser(container.getUsername());
    ds.setPassword(container.getPassword());
    jdbc = new JdbcTemplate(ds);
    jdbc.execute("TRUNCATE inbox,ledger,receipt_outbox");
    ledger =
        new JdbcLedger(
            jdbc,
            new TransactionTemplate(new DataSourceTransactionManager(ds)),
            JsonMapper.builder().build());
  }

  SettlementEvent event() {
    return new SettlementEvent(
        "event-id",
        UUID.randomUUID(),
        "merchant-demo",
        new BigDecimal("25.50"),
        "PEN",
        "ACCEPTED",
        "AX1",
        1);
  }

  @Test
  void repeatedDeliveryDoesNotDuplicateBalancedLedger() {
    var event = event();
    assertThat(ledger.accept(event)).isTrue();
    assertThat(ledger.accept(event)).isFalse();
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM ledger", Long.class)).isEqualTo(2L);
    assertThat(jdbc.queryForObject("SELECT SUM(amount) FROM ledger", BigDecimal.class))
        .isEqualByComparingTo("0");
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM receipt_outbox", Long.class))
        .isEqualTo(1L);
  }

  @Test
  void rejectedCreditRollsBackInboxAndDebit() {
    jdbc.execute("ALTER TABLE ledger ADD CONSTRAINT test_positive CHECK (amount > 0)");
    try {
      assertThatThrownBy(() -> ledger.accept(event())).isInstanceOf(RuntimeException.class);
      assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM inbox", Long.class)).isZero();
      assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM ledger", Long.class)).isZero();
    } finally {
      jdbc.execute("ALTER TABLE ledger DROP CONSTRAINT test_positive");
    }
  }
}
