package pe.axiz.platform.configuration;

import java.sql.Connection;
import java.sql.SQLException;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.jdbc.datasource.AbstractDataSource;
import software.amazon.awssdk.services.rds.RdsUtilities;

public final class IamDataSource extends AbstractDataSource {
  private final PGSimpleDataSource delegate;
  private final RdsUtilities rds;
  private final String host;
  private final int port;
  private final String username;

  public IamDataSource(
      PGSimpleDataSource delegate, RdsUtilities rds, String host, int port, String username) {
    this.delegate = delegate;
    this.rds = rds;
    this.host = host;
    this.port = port;
    this.username = username;
  }

  @Override
  public Connection getConnection() throws SQLException {
    // Se genera un token nuevo para cada conexión física; no se conserva como contraseña del pool.
    String token =
        rds.generateAuthenticationToken(b -> b.hostname(host).port(port).username(username));
    return delegate.getConnection(username, token);
  }

  @Override
  public Connection getConnection(String user, String password) throws SQLException {
    throw new SQLException("Se requiere autenticación IAM administrada");
  }
}
