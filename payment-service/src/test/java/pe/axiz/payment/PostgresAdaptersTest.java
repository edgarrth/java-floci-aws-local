package pe.axiz.payment;

import static org.assertj.core.api.Assertions.*;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.postgresql.PostgreSQLContainer;
import pe.axiz.payment.adapters.out.postgresql.JdbcPaymentStore;
import pe.axiz.payment.domain.*;
import tools.jackson.databind.json.JsonMapper;

@EnabledIfEnvironmentVariable(named = "RUN_POSTGRES_IT", matches = "true")
class PostgresAdaptersTest {
  private static PostgreSQLContainer container;
  private JdbcTemplate jdbc;
  private JdbcPaymentStore store;

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
    jdbc.execute("TRUNCATE outbox, payments CASCADE");
    store =
        new JdbcPaymentStore(
            jdbc,
            new TransactionTemplate(new DataSourceTransactionManager(ds)),
            JsonMapper.builder().build());
  }

  Payment pending() {
    return new Payment(
        UUID.randomUUID(),
        "merchant-demo",
        new Money(new BigDecimal("25.50"), "PEN"),
        "tok_demo",
        "integration-key",
        "fingerprint",
        Payment.Status.PENDING,
        null,
        Instant.now());
  }

  @Test
  void reserveIsUniqueAndDecisionOutboxIsAtomic() {
    var p = pending();
    assertThat(store.reserve(p).created()).isTrue();
    assertThat(store.reserve(p).created()).isFalse();
    var finalPayment = p.complete(Payment.Status.AUTHORIZED, "AX1");
    store.complete(finalPayment);
    store.complete(finalPayment);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM payments", Long.class)).isEqualTo(1L);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox", Long.class)).isEqualTo(3L);
  }

  @Test
  void failureDuringOutboxRollsBackPaymentDecision() {
    var p = pending();
    store.reserve(p);
    jdbc.execute(
        "ALTER TABLE outbox ADD CONSTRAINT test_destination CHECK (destination <> 'EVENTBRIDGE')");
    try {
      assertThatThrownBy(() -> store.complete(p.complete(Payment.Status.AUTHORIZED, "AX1")))
          .isInstanceOf(RuntimeException.class);
      assertThat(store.find(p.id()).orElseThrow().status()).isEqualTo(Payment.Status.PENDING);
      assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox", Long.class)).isZero();
    } finally {
      jdbc.execute("ALTER TABLE outbox DROP CONSTRAINT test_destination");
    }
  }
}
