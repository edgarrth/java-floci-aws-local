package pe.axiz.platform.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class DatabaseIdentityTest {
  @ParameterizedTest
  @CsvSource({
    "sslmode=verify-full,verify-full,false",
    "sslmode=verify-full,verify-full,true",
    "sslmode=disable,disable,false",
    "sslmode=prefer,prefer,false",
    "sslmode=require,require,true",
    "sslmode=verify-ca,verify-ca,true",
    "ssl=true,verify-full,false"
  })
  void separatesClientPolicyFromBackendTransport(String query, String mode, boolean backendTls)
      throws Exception {
    var connection = mock(Connection.class);
    var metadata = mock(DatabaseMetaData.class);
    var statement = mock(Statement.class);
    var result = mock(ResultSet.class);
    when(connection.getMetaData()).thenReturn(metadata);
    when(metadata.getURL()).thenReturn("jdbc:postgresql://floci:7001/axiz?" + query);
    when(connection.createStatement()).thenReturn(statement);
    when(statement.executeQuery(org.mockito.ArgumentMatchers.anyString())).thenReturn(result);
    when(result.next()).thenReturn(true);
    when(result.getString("username")).thenReturn("axiz_payment");
    when(result.getString("schema")).thenReturn("payment");
    when(result.getObject("backend_tls", Boolean.class)).thenReturn(backendTls);

    assertThat(DatabaseIdentity.inspect(connection))
        .isEqualTo(new DatabaseIdentity("axiz_payment", "payment", mode, backendTls));
    verify(result).close();
    verify(statement).close();
  }

  @Test
  void refusesAnUnknownDriverInsteadOfReportingAnUnverifiedTlsMode() throws Exception {
    var connection = mock(Connection.class);
    var metadata = mock(DatabaseMetaData.class);
    when(connection.getMetaData()).thenReturn(metadata);
    when(metadata.getURL()).thenReturn("jdbc:otro://localhost/db");

    assertThatThrownBy(() -> DatabaseIdentity.inspect(connection))
        .isInstanceOf(SQLException.class)
        .hasMessageContaining("configuración PostgreSQL");
  }
}
