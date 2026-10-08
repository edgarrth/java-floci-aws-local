package pe.axiz.platform.configuration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockConstruction;
import static org.mockito.Mockito.when;

import com.zaxxer.hikari.HikariDataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.mock.env.MockEnvironment;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;

class DatabaseConfigurationTest {
  @ParameterizedTest
  @ValueSource(strings = {"payment", "settlement"})
  void everyPhysicalConnectionReceivesItsSchemaAfterAuthentication(String schema) throws Exception {
    try (var driver =
            mockConstruction(
                PGSimpleDataSource.class,
                (pg, context) -> {
                  // Se reproduce una conexión cuyo proxy descartó el esquema del mensaje inicial.
                  when(pg.getConnection(anyString(), anyString()))
                      .thenAnswer(invocation -> physicalConnection());
                });
        var pool = pool(schema);
        var first = pool.getConnection();
        var second = pool.getConnection()) {
      assertThat(first.getSchema()).isEqualTo(schema);
      assertThat(second.getSchema()).isEqualTo(schema);
      assertThat(first.unwrap(Connection.class)).isNotSameAs(second.unwrap(Connection.class));
      assertThat(driver.constructed()).hasSize(1);
    }
  }

  @Test
  void returningConnectionRestoresServiceSchemaBeforeReuse() throws Exception {
    var physical = physicalConnection();
    try (var driver =
            mockConstruction(
                PGSimpleDataSource.class,
                (pg, context) -> {
                  when(pg.getConnection(anyString(), anyString())).thenReturn(physical);
                });
        var pool = pool("payment")) {
      try (var borrowed = pool.getConnection()) {
        borrowed.setSchema("public");
        assertThat(borrowed.getSchema()).isEqualTo("public");
      }
      try (var reused = pool.getConnection()) {
        assertThat(reused.unwrap(Connection.class)).isSameAs(physical);
        assertThat(reused.getSchema()).isEqualTo("payment");
      }
      assertThat(driver.constructed()).hasSize(1);
    }
  }

  private static HikariDataSource pool(String schema) {
    var env =
        new MockEnvironment()
            .withProperty("axiz.database.host", "aurora.local")
            .withProperty("axiz.database.port", "5432")
            .withProperty("axiz.database.name", "payments")
            .withProperty("axiz.database.user", "app_" + schema)
            .withProperty("axiz.database.ca", "/certs/root.crt")
            .withProperty("axiz.database.schema", schema)
            .withProperty("axiz.aws.region", "us-east-1")
            .withProperty("spring.application.name", schema + "-test");
    var credentials = StaticCredentialsProvider.create(AwsBasicCredentials.create("test", "test"));
    return (HikariDataSource) new DatabaseConfiguration().dataSource(env, credentials);
  }

  private static Connection physicalConnection() throws SQLException {
    var connection = mock(Connection.class);
    var schema = new AtomicReference<>("public");
    when(connection.getAutoCommit()).thenReturn(true);
    when(connection.isValid(5)).thenReturn(true);
    when(connection.getTransactionIsolation()).thenReturn(Connection.TRANSACTION_READ_COMMITTED);
    when(connection.getSchema()).thenAnswer(invocation -> schema.get());
    when(connection.isWrapperFor(Connection.class)).thenReturn(true);
    when(connection.unwrap(Connection.class)).thenReturn(connection);
    doAnswer(
            invocation -> {
              schema.set(invocation.getArgument(0));
              return null;
            })
        .when(connection)
        .setSchema(anyString());
    return connection;
  }
}
